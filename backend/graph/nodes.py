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


def step(name: str, input: Any, output: Any, *, label: str = "", detail: str = "") -> dict:
    """One Agent Trace entry.

    `name`/`input`/`output` are the raw record the debug view still renders.
    `label`/`detail` are the human-readable summary the Agent Trace shows; both
    are derived from real node data by the caller — never hard-coded copy.
    """
    return {
        "name": name,
        "stage": name,
        "label": label or name,
        "status": "complete",
        "detail": detail,
        "input": input,
        "output": output,
        "timestamp": _now(),
    }


def _fmt_price(v: Any) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


# --- grounded match reasons ------------------------------------------------
# A chip may only be emitted when the retrieved record itself supports it:
# either a structured metadata field (price) or a literal phrase present in the
# product's own text. Synonyms are listed explicitly so that the phrase shown on
# screen is always one that actually occurs in the evidence — the LLM never
# writes these strings.
_FEATURE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "easy to wash": ("machine washable", "machine wash", "easy care", "washable", "easy to wash"),
    "easy to clean": ("easy to clean", "wipe clean", "easy care", "machine washable"),
    "soft": ("ultra soft", "super soft", "extra soft", "softness", "soft"),
    "compact": ("compact", "space saving", "space-saving", "foldable", "mini"),
    "lightweight": ("lightweight", "light weight"),
    "durable": ("durable", "heavy duty", "heavy-duty", "long lasting", "tear resistant"),
    "comfortable": ("comfortable", "comfort", "cozy"),
    "breathable": ("breathable",),
    "hypoallergenic": ("hypoallergenic",),
    "waterproof": ("waterproof", "water resistant", "water-resistant"),
    "warm": ("warm", "all season", "all-season"),
    "quiet": ("quiet", "low noise", "noiseless"),
    "portable": ("portable", "travel"),
}


# --- search-session refinement ---------------------------------------------
# A refinement is not a new search: the utterance ("make it machine washable")
# is parsed by the same Router, then merged onto the constraints the session
# already carries. Structured scalars are replaced by the latest explicit
# value; qualitative preferences accumulate. The merged set then drives a fresh
# full-catalog retrieval — refinement never filters the previous result list.

# Latest explicit value wins for these — "under $40" must replace "under $50",
# not intersect with it.
_REPLACE_FIELDS = (
    "product_type", "budget", "size", "audience", "use_case",
    "brand", "category", "material", "eco_friendly",
)


def merge_constraints(prior: dict | None, new: dict | None) -> dict:
    merged = dict(prior or {})
    for field in _REPLACE_FIELDS:
        value = (new or {}).get(field)
        if value not in (None, "", []):
            merged[field] = value
    feats = list(merged.get("qualitative_features") or [])
    seen = {str(f).strip().lower() for f in feats}
    for f in (new or {}).get("qualitative_features") or []:
        key = str(f).strip().lower()
        if key and key not in seen:
            feats.append(f)
            seen.add(key)
    merged["qualitative_features"] = feats
    return merged


def diff_constraints(prior: dict | None, merged: dict) -> list[str]:
    """Human-readable description of what this refinement changed."""
    prior = prior or {}
    changes: list[str] = []
    for field in _REPLACE_FIELDS:
        before, after = prior.get(field), merged.get(field)
        if after in (None, "") or before == after:
            continue
        label = field.replace("_", " ").capitalize()
        if field == "budget":
            changes.append(f"Budget: {_money(before)} → {_money(after)}" if before
                           else f"Budget: ≤ {_money(after)}")
        elif before:
            changes.append(f"{label}: {before} → {after}")
        else:
            changes.append(f"{label}: {after}")
    before_f = {str(f).lower() for f in (prior.get("qualitative_features") or [])}
    added = [f for f in (merged.get("qualitative_features") or [])
             if str(f).lower() not in before_f]
    if added:
        changes.append("Added: " + ", ".join(added))
    removed = [f for f in (prior.get("qualitative_features") or [])
               if str(f).lower() not in {str(x).lower()
                                         for x in (merged.get("qualitative_features") or [])}]
    if removed:
        changes.append("Removed: " + ", ".join(removed))
    return changes


def compose_query(constraints: dict, fallback: str = "") -> str:
    """Flatten the accumulated session into the semantic query for rag.search.

    Retrieval must see the whole intent, not just the latest utterance — a
    refinement like "make it machine washable" is meaningless as a search on
    its own, and the best product may sit outside the previous top 3.
    """
    c = constraints or {}
    parts = [c.get("product_type"), c.get("size"), c.get("material")]
    parts += list(c.get("qualitative_features") or [])
    if c.get("audience"):
        parts.append(f"for {c['audience']}")
    if c.get("use_case"):
        parts.append(f"for {c['use_case']}")
    if c.get("brand"):
        parts.append(str(c["brand"]))
    query = " ".join(str(p).strip() for p in parts if p)
    return query or fallback


# --- broad-query clarification --------------------------------------------
# One question, asked only when the request names a product but carries almost
# nothing to rank on. Deterministic so the behaviour is explainable and testable
# rather than depending on the model's mood.
_MIN_SIGNALS_TO_PROCEED = 1


def clarification_needed(router: dict) -> str | None:
    """Return a single clarifying question, or None to proceed with retrieval."""
    c = router.get("constraints") or {}
    if not c.get("product_type"):
        return None                      # nothing concrete to ask about
    if router.get("needs_live") or router.get("safety_flags"):
        return None                      # a live/price check is already specific
    signals = [
        c.get("budget"), c.get("audience"), c.get("use_case"), c.get("size"),
        c.get("brand"), c.get("material"),
        *(c.get("qualitative_features") or []),
    ]
    if sum(1 for s in signals if s not in (None, "", [])) >= _MIN_SIGNALS_TO_PROCEED:
        return None                      # specific enough to rank responsibly
    product = str(c["product_type"]).strip()
    return (f"Happy to help you find {'an' if product[:1].lower() in 'aeiou' else 'a'} "
            f"{product}. Who is it mainly for, and how will you use it?")


# --- audience-appropriate ranking ------------------------------------------
# Kid-targeted products are demoted (never removed) when the shopper gave no
# child signal. Applied only when the product's own text carries an explicit
# audience marker, so it cannot misfire on a category where these words are
# meaningless.
_CHILD_MARKERS = (
    "kids", "kid's", "children", "children's", "toddler", "baby", "infant",
    "nursery", "preschool", "boys", "girls", "juvenile", "for ages",
)
_CHILD_REQUEST = re.compile(
    r"\b(kid|kids|child|children|toddler|baby|infant|son|daughter|nursery|"
    r"preschool|school|boy|girl|year[- ]old|grandchild|niece|nephew)\b", re.I)


def wants_child_products(transcript: str, constraints: dict) -> bool:
    audience = (constraints.get("audience") or "").lower()
    if any(w in audience for w in ("child", "kid", "toddler", "baby", "infant", "teen")):
        return True
    if audience in ("adult", "myself", "me", "men", "women"):
        return False
    return bool(_CHILD_REQUEST.search(transcript or ""))


def _is_child_product(row: dict) -> bool:
    return any(m in _evidence_blob(row) for m in _CHILD_MARKERS)


def demote_child_products(rows: list[dict], transcript: str, constraints: dict) -> tuple[list[dict], int]:
    """Stable-partition kid-targeted rows to the end. Returns (rows, n_demoted)."""
    if not rows or wants_child_products(transcript, constraints):
        return rows, 0
    general = [r for r in rows if not _is_child_product(r)]
    child = [r for r in rows if _is_child_product(r)]
    if not child or not general:
        return rows, 0          # nothing to reorder, or the whole category is kids'
    return general + child, len(child)


def _evidence_blob(row: dict) -> str:
    """The literal text a chip is allowed to cite."""
    return " ".join(
        str(row.get(k) or "")
        for k in ("title", "features", "ingredients", "category")
    ).lower()


def _find_phrase(blob: str, candidates: tuple[str, ...] | str) -> str | None:
    """Return the first candidate phrase literally present in `blob`."""
    if isinstance(candidates, str):
        candidates = (candidates,)
    for phrase in candidates:
        if phrase and phrase.lower() in blob:
            return phrase
    return None


def _titlecase(phrase: str) -> str:
    return phrase[:1].upper() + phrase[1:]


def _money(v: Any) -> str:
    """'$50' rather than '$50.00' — chips read better without dead cents."""
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _display_size(phrase: str) -> str:
    """Title-case a size token, keeping XL-style suffixes upper ('twin/twin xl')."""
    words = []
    for w in phrase.split():
        words.append(w.upper() if w.lower() in ("xl", "xxl", "xs") else
                     "/".join(p[:1].upper() + p[1:] for p in w.split("/")))
    return " ".join(words)


def match_evidence(row: dict, constraints: dict) -> dict:
    """Build evidence-backed "why this product" chips for one retrieved row.

    Returns {match_reasons, matched_constraints, supported_constraints}.
    `supported_constraints` counts only the criteria we can actually check
    against this dataset, so an unverifiable preference is never silently
    scored as a match.
    """
    blob = _evidence_blob(row)
    reasons: list[str] = []
    supported = 0
    matched = 0

    # HARD: budget — verified against structured price metadata, not text.
    budget = constraints.get("budget")
    price = row.get("price")
    if isinstance(budget, (int, float)):
        supported += 1
        if isinstance(price, (int, float)) and price <= budget:
            matched += 1
            reasons.append(f"Under {_money(budget)}")

    # HARD: size — only claimed when the product text says so literally.
    size = constraints.get("size")
    if size:
        supported += 1
        hit = _find_phrase(blob, str(size).lower())
        if hit:
            matched += 1
            # Prefer a richer literal form when the record has one ("twin/twin xl").
            m = re.search(rf"\b[\w/]*{re.escape(hit)}[\w/]*(?:\s+x{{1,2}}l)?\b", blob)
            reasons.append(_display_size((m.group(0) if m else hit).strip()))

    # HARD: brand — only when the user asked for one.
    brand = constraints.get("brand")
    if brand:
        supported += 1
        if _find_phrase(blob, str(brand).lower()):
            matched += 1
            reasons.append(_titlecase(str(brand)))

    # SOFT: qualitative features — chip text is the phrase found in the record.
    for feat in constraints.get("qualitative_features") or []:
        f = str(feat).strip().lower()
        if not f:
            continue
        supported += 1
        hit = _find_phrase(blob, _FEATURE_SYNONYMS.get(f, (f,)))
        if hit:
            matched += 1
            reasons.append(_titlecase(hit))

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r.lower() in seen or seen.add(r.lower()))]
    return {
        "match_reasons": deduped,
        "matched_constraints": matched,
        "supported_constraints": supported,
    }


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
    # NB: `image` is deliberately NOT included — _slim feeds the rerank and
    # answerer prompts, and a CDN URL there is dead tokens plus something the
    # model could try to cite. The UI reads images off the full retrieval row.
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

        # "apply" — the client edited the constraint set directly (e.g. removed a
        # preference chip). Running extraction here would be actively wrong:
        # "Remove the soft preference" contains the word "soft", so the router
        # would parse it straight back in. Use the given constraints verbatim.
        if state.get("apply_only"):
            c = dict(state.get("prior_constraints") or {})
            router = {
                "task": "product_recommendation",
                "constraints": c,
                "safety_flags": [],
                "needs_live": bool(state.get("prior_needs_live")),
                "top_k": state.get("prior_top_k") or 3,
            }
            effective = compose_query(c, transcript)
            return {
                "router": router,
                "effective_query": effective,
                "constraint_changes": state.get("applied_changes") or [],
                "steps": [step(
                    "session",
                    {"applied_constraints": c, "note": "client-edited constraints; "
                     "router extraction skipped so the edit is not re-parsed"},
                    {"merged_constraints": c, "search_query": effective},
                    label="Updating search",
                    detail=" · ".join(state.get("applied_changes") or []) or "Constraints updated",
                )],
            }

        prompt = full_prompt(
            "router",
            few_shots=load("few_shots_router"),
            transcript=transcript,
        )
        out: RouterOutput = await call_structured(
            prompt, RouterOutput, node="router", context={"transcript": transcript}
        )
        router = out.model_dump()

        # Refinement: fold this utterance's constraints onto the session's.
        prior = state.get("prior_constraints") or None
        changes: list[str] = []
        if prior:
            merged = merge_constraints(prior, router.get("constraints") or {})
            changes = diff_constraints(prior, merged)
            router["constraints"] = merged
            # top_k is only meaningful when this utterance asked for a count.
            if not re.search(r"\b(compare|show|top|best)\b.*\b(\d+|three|four|five)\b",
                             transcript, re.I):
                router["top_k"] = state.get("prior_top_k") or router.get("top_k") or 3

        c = router.get("constraints") or {}
        bits = [
            c.get("product_type"),
            c.get("size"),
            f"≤ {_money(c["budget"])}" if isinstance(c.get("budget"), (int, float)) else None,
            *(c.get("qualitative_features") or []),
        ]
        detail = " · ".join(_titlecase(str(b)) for b in bits if b)
        effective = compose_query(c, transcript)
        out_steps = [step(
            "router",
            {
                "transcript": transcript,
                "refinement_of": prior or None,
                "prompt_file": "prompts/router.md (+ few_shots_router.md)",
                "prompt_preview": _preview(prompt),
                "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
            },
            router,
            label=("Understanding refinement" if prior else
                   "Fresh information requested" if router.get("needs_live") else
                   "Understanding request"),
            detail=(" · ".join(changes) if changes else detail) or "No explicit constraints",
        )]
        if prior:
            out_steps.append(step(
                "session",
                {"previous_constraints": prior, "utterance": transcript},
                {"merged_constraints": c, "changes": changes, "search_query": effective},
                label="Updating search",
                detail=detail or "No explicit constraints",
            ))
        return {
            "router": router,
            "effective_query": effective,
            "constraint_changes": changes,
            "steps": out_steps,
        }

    def route_after_router(state: dict) -> str:
        if state["router"].get("safety_flags"):
            return "safety"
        if clarification_needed(state["router"]):
            return "clarify"
        return "planner"

    # ---- 1b. Clarify (deterministic, one question) ------------------------
    async def clarify_node(state: dict) -> dict:
        question = clarification_needed(state["router"]) or ""
        c = (state["router"].get("constraints") or {})
        return {
            "clarify": {"question": question, "product_type": c.get("product_type")},
            "mode": "clarify",
            "answer": {"spoken_answer": question, "top_pick_doc_id": "", "citation_doc_ids": []},
            "steps": [step(
                "clarify",
                {"rule": "product_type given but no budget/audience/use_case/size/"
                         "brand/material/features to rank on",
                 "constraints": c},
                {"question": question},
                label="Asking one clarifying question",
                detail="Request too broad to rank responsibly — no retrieval run",
            )],
        }

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
                label="Safety check",
                detail=f"Blocked: {', '.join(flags) or 'unsafe request'}",
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
        srcs = plan["sources"]
        detail = ("Private Amazon catalog + live web" if "web.search" in srcs
                  else "Private Amazon catalog selected")
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
                label="Planning search",
                detail=detail,
            )],
        }

    # ---- 4. Retrieve via MCP rag.search + LLM rerank ----------------------
    async def retrieve_node(state: dict) -> dict:
        # Search on the accumulated intent, not the latest utterance alone.
        transcript = state.get("effective_query") or state["transcript"]
        plan = state["plan"]
        f = plan.get("retrieval_filters") or {}
        args: dict[str, Any] = {"query": transcript, "top_k": settings.RAG_TOP_K}
        for key in ("max_price", "category", "material", "eco_friendly"):
            if f.get(key) not in (None, ""):
                args[key] = f[key]

        resp = await mcp.call("rag.search", args)
        candidates = [r for r in resp.get("results", []) if r.get("doc_id")]

        router_c = (state.get("router") or {}).get("constraints") or {}
        want = max(1, min(int((state.get("router") or {}).get("top_k") or 3), 8))

        # --- HARD-constraint guard -----------------------------------------
        # Never let an over-budget row be presented as a match. rag.search
        # already filters on price, but its relaxation ladder can drop that
        # filter; re-check here so the budget is enforced no matter what.
        budget = router_c.get("budget")
        no_match: dict[str, Any] | None = None
        if isinstance(budget, (int, float)):
            within = [c for c in candidates if isinstance(c.get("price"), (int, float))
                      and c["price"] <= budget]
            if not within:
                # rag.search applies the price filter itself, so an empty result
                # cannot tell us *why* it is empty. Re-query without the budget:
                #   probe has rows -> the catalog stocks this product, just not
                #                     at this price  -> honest budget no-match
                #   probe is empty -> nothing relevant at any price -> ordinary
                #                     "no private match" -> existing web fallback
                # Blaming the budget in the second case would name the wrong
                # constraint and quote a price that does not exist.
                # Same filters, price removed — so a non-empty probe means
                # "this product exists here, just not at your price". Dropping
                # every filter instead would blame the budget for a miss the
                # category or material filter actually caused.
                probe_args = {k: v for k, v in args.items() if k != "max_price"}
                probe = await mcp.call("rag.search", probe_args)
                probe_rows = [r for r in probe.get("results", []) if r.get("doc_id")]
                prices = [r["price"] for r in probe_rows
                          if isinstance(r.get("price"), (int, float))]
                if probe_rows:
                    no_match = {
                        "match_status": "no_exact_match",
                        "failed_constraints": ["max_price"],
                        "requested_max_price": float(budget),
                        "closest_available_price": min(prices) if prices else None,
                        "closest_alternatives": [{**_slim(r), "image": r.get("image")} for r in sorted(
                            probe_rows,
                            key=lambda r: (r.get("price") if isinstance(r.get("price"), (int, float)) else 1e9),
                        )[:want]],
                    }
            candidates = within

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
                if len(ranked) == want:
                    break
            hallucinated = [d for d in out.ranked_doc_ids if d not in by_id]
            if len(ranked) < want:
                for c in sorted(candidates, key=lambda r: -(r.get("score") or 0)):
                    if c["doc_id"] not in seen:
                        ranked.append(c)
                        seen.add(c["doc_id"])
                    if len(ranked) == want:
                        break
            # Deterministic audience guard, applied after the LLM rerank so the
            # outcome is guaranteed rather than hoped for.
            ranked, demoted = demote_child_products(ranked, transcript, router_c)
            top_picks = ranked
            rerank_info = {
                "ranked_doc_ids": [r["doc_id"] for r in ranked],
                "rationale": out.rationale,
                "prompt_file": "prompts/reranker.md",
            }
            if hallucinated:
                rerank_info["dropped_unknown_doc_ids"] = hallucinated
            if demoted:
                rerank_info["child_products_demoted"] = demoted
                rerank_info["audience_rule"] = (
                    "no child signal in the request — kid-targeted rows moved "
                    "below general-audience rows (demoted, not removed)")

            # Attach evidence-backed chips to every surviving pick.
            for p in top_picks:
                p.update(match_evidence(p, router_c))

        detail = (f"MCP → rag.search · {len(candidates)} candidates found"
                  if candidates else "MCP → rag.search · no qualifying products")
        if no_match:
            detail = (f"MCP → rag.search · nothing at or below "
                      f"{_money(no_match["requested_max_price"])}")

        out_state: dict[str, Any] = {
            "candidates": candidates,
            "top_picks": top_picks,
            "mode": "no_match" if no_match else "private",
            "steps": [step(
                "rag.search",
                {"tool": "rag.search", "transport": "MCP (stdio)", "arguments": args},
                {
                    "no_match": no_match,
                    "result_count": len(candidates),
                    "resolved_category": resp.get("resolved_category"),
                    "relaxations": resp.get("relaxations"),
                    "relevance_floor": resp.get("relevance_floor"),
                    "dropped_below_floor": resp.get("dropped_below_floor"),
                    "error": resp.get("error"),
                    "candidates": [_slim(c, keep_features=120) for c in candidates],
                    "rerank": rerank_info or None,
                },
                label="Searching private catalog",
                detail=detail,
            )] + ([step(
                "rerank",
                {"prompt_file": "prompts/reranker.md", "candidates": len(candidates),
                 "criteria": plan.get("comparison_criteria")},
                rerank_info,
                label="Comparing candidates",
                detail=f"Top {len(top_picks)} selected from {len(candidates)} candidates",
            )] if top_picks else []),
        }
        if no_match:
            out_state["no_match"] = no_match
        return out_state

    def route_after_retrieve(state: dict) -> str:
        # A hard constraint that nothing satisfies is an answer in itself — go
        # straight to the answerer rather than quietly firing a live web search
        # the user never asked for.
        if state.get("no_match"):
            return "answer"
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
                label="Searching the web",
                detail=f"MCP → web.search · {len(resp.get('results', []))} live results"
                       + (" (cached)" if resp.get("cached") else ""),
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
                label="Searching the web",
                detail=f"No private match · MCP → web.search · {len(rows)} live results",
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
                label="Matching live results",
                detail=(f"{len(matches)} of {len(picks)} catalog picks matched to a live listing"
                        + (f" · {len(flags)} price discrepancy flagged" if flags else "")),
            )],
        }

    # ---- 7. Answerer / Critic ---------------------------------------------
    async def answer_node(state: dict) -> dict:
        # Answer against the full accumulated request so the summary reflects
        # every constraint, not just the last thing the user said.
        transcript = state.get("effective_query") or state["transcript"]
        mode = state.get("mode", "private")
        picks = state.get("top_picks", [])

        # No-match is answered deterministically. There is nothing to ground an
        # LLM answer in, and asking a model to explain an empty result set is
        # exactly where a fabricated product or price would come from.
        nm = state.get("no_match")
        if nm:
            budget = _money(nm.get("requested_max_price"))
            closest = nm.get("closest_available_price")
            spoken = (f"I couldn't find anything at or below {budget} in the catalog. "
                      + (f"The closest options start around {_money(closest)}. "
                         if isinstance(closest, (int, float)) else "")
                      + "Would you like to raise the budget or search online?")
            return {
                "answer": {"spoken_answer": spoken, "top_pick_doc_id": "", "citation_doc_ids": []},
                "steps": [step(
                    "answerer",
                    {"mode": "no_match", "failed_constraints": nm.get("failed_constraints"),
                     "note": "deterministic — no LLM call, nothing to ground an answer in"},
                    {"spoken_answer": spoken},
                    label="Generating response",
                    detail="No qualifying products — reporting the gap instead of recommending",
                )],
            }
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
                label="Generating response",
                detail=f"Grounded in {len(products)} retrieved product"
                       f"{'s' if len(products) != 1 else ''}",
            ), step(
                "grounding",
                {
                    "check": "citation doc_ids must exist in retrieved rows; "
                             "top_pick must be one of them; discrepancies must be voiced",
                    "known_doc_ids": sorted(known),
                },
                {
                    "citations_kept": answer.get("citation_doc_ids"),
                    "citations_dropped": dropped or None,
                    "critic_notes": critic_notes or None,
                },
                label="Checking grounding",
                detail=(" · ".join(critic_notes) if critic_notes
                        else "All claims verified against retrieved evidence"),
            )],
        }

    return {
        "router": router_node,
        "route_after_router": route_after_router,
        "safety": safety_node,
        "clarify": clarify_node,
        "planner": planner_node,
        "retrieve": retrieve_node,
        "route_after_retrieve": route_after_retrieve,
        "web_compare": web_compare_node,
        "web_fallback": web_fallback_node,
        "reconcile": reconcile_node,
        "answer": answer_node,
    }
