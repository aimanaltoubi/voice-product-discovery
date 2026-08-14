"""LangGraph node implementations.

Pipeline: router -> (safety | planner) -> retrieve[rag.search + rerank]
          -> (web_compare -> reconcile | web_fallback | direct) -> answerer.

Design notes
------------
- Nodes are built by `build_nodes(mcp)` so they close over the MCP client;
  all tool access goes through the Model Context Protocol, never by import.
- Every node appends an entry to `steps` (name/input/output/timestamp) —
  that list is rendered verbatim by the UI's Agent Step Log. Step names
  match the frontend's STEP_LABELS map.
- LLM outputs are validated against the Pydantic schemas in state.py and
  then re-checked deterministically (planner rubric enforcement, rerank
  subset check, answer grounding check). The LLM proposes; code verifies.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from graph.llm import call_structured
from graph.prompts import full_prompt, load
from graph.state import (
    AnswerOutput,
    PlanOutput,
    RerankOutput,
    RouterOutput,
)
from mcp_server.client import MCPToolClient

PROMPT_PREVIEW_CHARS = 800


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def step(name: str, input: Any, output: Any) -> dict:
    return {"name": name, "input": input, "output": output, "timestamp": _now()}


def _preview(prompt: str) -> str:
    return prompt if len(prompt) <= PROMPT_PREVIEW_CHARS else prompt[:PROMPT_PREVIEW_CHARS] + " …[truncated]"


def _slim(row: dict, *, keep_features: int = 220) -> dict:
    """Reduce a catalog/web row to the fields the LLM and UI need."""
    out = {
        "doc_id": row.get("doc_id"),
        "sku": row.get("sku"),
        "title": row.get("title"),
        "brand": row.get("brand"),
        "price": row.get("price"),
        "rating": row.get("rating"),
        "price_per_oz": row.get("price_per_oz"),
        "eco_friendly": row.get("eco_friendly"),
        "ingredients": (row.get("ingredients") or None),
        "features": (row.get("features") or "")[:keep_features] or None,
    }
    if row.get("url"):
        out["url"] = row["url"]
    if row.get("availability"):
        out["availability"] = row["availability"]
    return {k: v for k, v in out.items() if v is not None or k in ("price", "rating", "brand")}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


_PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:[.,]\d{2})?)")


def _price_from_text(*texts: str) -> float | None:
    for t in texts:
        m = _PRICE_RE.search(t or "")
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# node factory
# --------------------------------------------------------------------------

def build_nodes(mcp: MCPToolClient) -> dict:
    """Return the node callables, closed over the shared MCP client."""

    # ---- 1. Router --------------------------------------------------------
    async def router_node(state: dict) -> dict:
        transcript = state["transcript"]
        prompt = full_prompt(
            "router",
            few_shots=load("few_shots_router"),
            transcript=transcript,
        )
        out: RouterOutput = await call_structured(
            prompt, RouterOutput, node="router", context={"transcript": transcript}
        )
        router = out.model_dump()
        return {
            "router": router,
            "steps": [step(
                "router",
                {
                    "transcript": transcript,
                    "prompt_file": "prompts/router.md (+ few_shots_router.md)",
                    "prompt_preview": _preview(prompt),
                    "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
                },
                router,
            )],
        }

    def route_after_router(state: dict) -> str:
        return "safety" if state["router"].get("safety_flags") else "planner"

    # ---- 2. Safety gate (deterministic) -----------------------------------
    async def safety_node(state: dict) -> dict:
        flags = state["router"].get("safety_flags", [])
        refusal = (
            "I can't help with that. Mixing or misusing household chemicals — like combining "
            "bleach and ammonia — can release toxic gases and cause serious harm. "
            "If exposure has happened, contact your local poison control service. "
            "I'm happy to recommend safe, ready-made cleaning products instead."
        )
        answer = {"spoken_answer": refusal, "top_pick_doc_id": "", "citation_doc_ids": []}
        return {
            "blocked": True,
            "answer": answer,
            "steps": [step(
                "safety",
                {"safety_flags": flags, "policy": "docs/safety.md — no unsafe chemical advice"},
                {"action": "blocked", "spoken_answer": refusal},
            )],
        }

    # ---- 3. Planner (LLM + deterministic rubric enforcement) ---------------
    async def planner_node(state: dict) -> dict:
        transcript = state["transcript"]
        router = state["router"]
        prompt = full_prompt(
            "planner",
            router_json=json.dumps(router, indent=2),
            transcript=transcript,
        )
        out: PlanOutput = await call_structured(
            prompt, PlanOutput, node="planner", context={"router": router}
        )
        plan = out.model_dump()
        enforced: list[str] = []

        # Rubric rule 1: rag.search is always the primary source of facts.
        if "rag.search" not in plan["sources"]:
            plan["sources"].insert(0, "rag.search")
            enforced.append("added rag.search (private catalog is always primary)")
        # Rubric rule 2: web.search iff the user asked for live info.
        if router.get("needs_live") and "web.search" not in plan["sources"]:
            plan["sources"].append("web.search")
            enforced.append("added web.search (router flagged needs_live)")
        if not router.get("needs_live") and "web.search" in plan["sources"]:
            plan["sources"] = [s for s in plan["sources"] if s != "web.search"]
            enforced.append("removed web.search (no live-data request; web is fallback-only)")

        # Merge router constraints into any filter slots the planner left empty.
        c = router.get("constraints") or {}
        f = plan["retrieval_filters"]
        merges = {
            "max_price": c.get("budget"),
            "category": c.get("category"),
            "material": c.get("material"),
            "eco_friendly": c.get("eco_friendly"),
        }
        for key, value in merges.items():
            if f.get(key) in (None, "") and value not in (None, ""):
                f[key] = value
                enforced.append(f"filled filter {key}={value!r} from router constraints")

        plan["enforced_rules"] = enforced
        return {
            "plan": plan,
            "steps": [step(
                "planner",
                {
                    "router_output": router,
                    "prompt_file": "prompts/planner.md",
                    "planner_rule": "prefer rag.search; add web.search only for current/latest/availability",
                },
                plan,
            )],
        }

    # ---- 4. Retrieve via MCP rag.search + LLM rerank ----------------------
    async def retrieve_node(state: dict) -> dict:
        transcript = state["transcript"]
        plan = state["plan"]
        f = plan.get("retrieval_filters") or {}
        args: dict[str, Any] = {"query": transcript, "top_k": settings.RAG_TOP_K}
        for key in ("max_price", "category", "material", "eco_friendly"):
            if f.get(key) not in (None, ""):
                args[key] = f[key]

        resp = await mcp.call("rag.search", args)
        candidates = [r for r in resp.get("results", []) if r.get("doc_id")]

        rerank_info: dict[str, Any] = {}
        top_picks: list[dict] = []
        if candidates:
            slim = [_slim(c) for c in candidates]
            prompt = full_prompt(
                "reranker",
                transcript=transcript,
                criteria_json=json.dumps(plan.get("comparison_criteria") or []),
                candidates_json=json.dumps(slim, indent=1),
            )
            out: RerankOutput = await call_structured(
                prompt, RerankOutput, node="reranker", context={"candidates": candidates}
            )
            # Deterministic validation: ranked ids must be a subset of the
            # candidates; dedupe; top up to 3 by retrieval score.
            by_id = {c["doc_id"]: c for c in candidates}
            ranked, seen = [], set()
            for doc_id in out.ranked_doc_ids:
                if doc_id in by_id and doc_id not in seen:
                    ranked.append(by_id[doc_id])
                    seen.add(doc_id)
                if len(ranked) == 3:
                    break
            hallucinated = [d for d in out.ranked_doc_ids if d not in by_id]
            if len(ranked) < 3:
                for c in sorted(candidates, key=lambda r: -(r.get("score") or 0)):
                    if c["doc_id"] not in seen:
                        ranked.append(c)
                        seen.add(c["doc_id"])
                    if len(ranked) == 3:
                        break
            top_picks = ranked
            rerank_info = {
                "ranked_doc_ids": [r["doc_id"] for r in ranked],
                "rationale": out.rationale,
                "prompt_file": "prompts/reranker.md",
            }
            if hallucinated:
                rerank_info["dropped_unknown_doc_ids"] = hallucinated

        return {
            "candidates": candidates,
            "top_picks": top_picks,
            "mode": "private",
            "steps": [step(
                "rag.search",
                {"tool": "rag.search", "transport": "MCP (stdio)", "arguments": args},
                {
                    "result_count": len(candidates),
                    "resolved_category": resp.get("resolved_category"),
                    "relaxations": resp.get("relaxations"),
                    "relevance_floor": resp.get("relevance_floor"),
                    "dropped_below_floor": resp.get("dropped_below_floor"),
                    "error": resp.get("error"),
                    "candidates": [_slim(c, keep_features=120) for c in candidates],
                    "rerank": rerank_info or None,
                },
            )],
        }

    def route_after_retrieve(state: dict) -> str:
        if not state.get("candidates"):
            return "web_fallback"
        if "web.search" in (state.get("plan", {}).get("sources") or []):
            return "web_compare"
        return "answer"

    # ---- 5a. Web compare (live price/availability for the top pick) -------
    async def web_compare_node(state: dict) -> dict:
        top = state["top_picks"][0]
        query = " ".join(x for x in [top.get("title"), top.get("brand"), "price"] if x)
        args = {"query": query, "max_results": 5}
        resp = await mcp.call("web.search", args)
        web = {"mode": "compare", **resp}
        return {
            "web": web,
            "steps": [step(
                "web.search",
                {
                    "tool": "web.search",
                    "transport": "MCP (stdio)",
                    "arguments": args,
                    "reason": "user asked for current price/availability (needs_live)",
                },
                {
                    "provider": resp.get("provider"),
                    "cached": resp.get("cached", False),
                    "result_count": len(resp.get("results", [])),
                    "results": resp.get("results", []),
                    "error": resp.get("error"),
                },
            )],
        }

    # ---- 5b. Web fallback (private catalog had no match) -------------------
    async def web_fallback_node(state: dict) -> dict:
        transcript = state["transcript"]
        args = {"query": f"{transcript} buy price", "max_results": 6}
        resp = await mcp.call("web.search", args)
        rows = []
        for i, r in enumerate(resp.get("results", [])):
            rows.append({
                "doc_id": f"web-{i + 1}",
                "sku": f"web-{i + 1}",
                "title": r.get("title"),
                "brand": None,
                "price": r.get("price") if isinstance(r.get("price"), (int, float))
                         else _price_from_text(r.get("snippet", ""), r.get("title", "")),
                "rating": None,
                "features": r.get("snippet"),
                "url": r.get("url"),
                "availability": r.get("availability"),
                "source": "web",
            })
        web = {"mode": "fallback", **resp}
        return {
            "web": web,
            "top_picks": rows[:3],
            "mode": "web_fallback",
            "steps": [step(
                "web.search",
                {
                    "tool": "web.search",
                    "transport": "MCP (stdio)",
                    "arguments": args,
                    "reason": "no_private_matches — falling back to live web results",
                },
                {
                    "provider": resp.get("provider"),
                    "cached": resp.get("cached", False),
                    "result_count": len(rows),
                    "results": resp.get("results", []),
                    "error": resp.get("error"),
                },
            )],
        }

    # ---- 6. Reconcile (deterministic conflict handling) --------------------
    async def reconcile_node(state: dict) -> dict:
        web_results = (state.get("web") or {}).get("results", [])
        picks = state.get("top_picks", [])
        matches: dict[str, Any] = {}
        flags: list[str] = []
        for pick in picks:
            pick_key = _norm(f"{pick.get('title', '')} {pick.get('brand') or ''}")
            best, best_ratio = None, 0.0
            for r in web_results:
                ratio = difflib.SequenceMatcher(
                    None, pick_key, _norm(r.get("title", ""))
                ).ratio()
                if ratio > best_ratio:
                    best, best_ratio = r, ratio
            if not best or best_ratio < 0.45:
                continue
            web_price = best.get("price") if isinstance(best.get("price"), (int, float)) \
                else _price_from_text(best.get("snippet", ""), best.get("title", ""))
            entry: dict[str, Any] = {
                "matched_by": "title/brand similarity (SKU-less web rows)",
                "similarity": round(best_ratio, 2),
                "web_title": best.get("title"),
                "web_url": best.get("url"),
                "web_price": web_price,
                "availability": best.get("availability"),
            }
            cat_price = pick.get("price")
            if isinstance(cat_price, (int, float)) and isinstance(web_price, (int, float)) and cat_price:
                delta_pct = round(abs(web_price - cat_price) / cat_price * 100, 1)
                entry["catalog_price"] = cat_price
                entry["price_delta_pct"] = delta_pct
                if delta_pct > 15:
                    entry["discrepancy"] = True
                    flags.append(
                        f"{pick.get('title')}: live price ${web_price:.2f} differs from "
                        f"catalog ${cat_price:.2f} ({delta_pct:.0f}%)"
                    )
            if best.get("availability"):
                entry.setdefault("notes", []).append(f"availability: {best['availability']}")
            matches[pick["doc_id"]] = entry

        reconciliation = {"matches": matches, "discrepancy_flags": flags}
        return {
            "reconciliation": reconciliation,
            "steps": [step(
                "reconcile",
                {
                    "method": "deterministic: normalized title+brand similarity >= 0.45; "
                              "price delta > 15% flagged",
                    "catalog_picks": [p.get("title") for p in picks],
                    "web_titles": [r.get("title") for r in web_results],
                },
                reconciliation,
            )],
        }

    # ---- 7. Answerer / Critic ---------------------------------------------
    async def answer_node(state: dict) -> dict:
        transcript = state["transcript"]
        mode = state.get("mode", "private")
        picks = state.get("top_picks", [])
        products = [_slim(p) for p in picks]
        reconciliation = state.get("reconciliation")
        web_block = None
        if reconciliation or (state.get("web") and mode == "private"):
            web_block = {
                "reconciliation": reconciliation,
                "live_results": (state.get("web") or {}).get("results", [])[:5],
            }
        criteria = (state.get("plan") or {}).get("comparison_criteria") or ["price", "rating"]

        prompt = full_prompt(
            "answerer",
            transcript=transcript,
            criteria_json=json.dumps(criteria),
            mode=mode,
            products_json=json.dumps(products, indent=1),
            web_json=json.dumps(web_block, indent=1) if web_block else "null",
        )
        out: AnswerOutput = await call_structured(
            prompt, AnswerOutput, node="answerer",
            context={"top_picks": products, "reconciliation": reconciliation},
        )
        answer = out.model_dump()
        critic_notes: list[str] = []

        # Grounding checks (the "critic checks grounding" duty, in code).
        known = {p["doc_id"] for p in products if p.get("doc_id")}
        cited = [d for d in answer.get("citation_doc_ids", []) if d in known]
        dropped = [d for d in answer.get("citation_doc_ids", []) if d not in known]
        if dropped:
            critic_notes.append(f"dropped uncited/unknown doc_ids: {dropped}")
        answer["citation_doc_ids"] = cited or sorted(known)
        if answer.get("top_pick_doc_id") not in known:
            fallback = products[0]["doc_id"] if products else ""
            if answer.get("top_pick_doc_id"):
                critic_notes.append(
                    f"top_pick_doc_id {answer['top_pick_doc_id']!r} not in rows; "
                    f"replaced with {fallback!r}")
            answer["top_pick_doc_id"] = fallback

        # Critic duty: discrepancies must be surfaced in the spoken answer.
        flags = (reconciliation or {}).get("discrepancy_flags") or []
        spoken = answer.get("spoken_answer", "")
        if flags and not re.search(r"\b(differ|discrepan|changed|higher|lower)\w*", spoken, re.I):
            answer["spoken_answer"] = spoken.rstrip() + " Note: the live web price differs from our catalog."
            critic_notes.append("appended discrepancy notice (reconciliation flagged a price conflict)")

        return {
            "answer": answer,
            "steps": [step(
                "answerer",
                {
                    "mode": mode,
                    "prompt_file": "prompts/answerer.md",
                    "product_rows": len(products),
                    "criteria": criteria,
                    "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
                },
                {**answer, "critic_notes": critic_notes or None},
            )],
        }

    return {
        "router": router_node,
        "route_after_router": route_after_router,
        "safety": safety_node,
        "planner": planner_node,
        "retrieve": retrieve_node,
        "route_after_retrieve": route_after_retrieve,
        "web_compare": web_compare_node,
        "web_fallback": web_fallback_node,
        "reconcile": reconcile_node,
        "answer": answer_node,
    }
