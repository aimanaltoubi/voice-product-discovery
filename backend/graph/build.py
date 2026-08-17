"""Graph assembly + the run_discovery entry point.

    router ──safety_flags──▶ safety ─▶ END
      │
      ▼
    planner ─▶ retrieve (rag.search + rerank)
                  │ no candidates ─▶ web_fallback ─▶ answer
                  │ needs_live    ─▶ web_compare ─▶ reconcile ─▶ answer
                  └ otherwise     ─────────────────────────────▶ answer ─▶ END

`run_discovery` compiles the graph once per MCP client and returns the
payload shape the React frontend consumes verbatim.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from langgraph.graph import END, StateGraph

from graph.nodes import _RATING_REQUEST_RE, _is_search_results_page, build_nodes, step
from graph.state import DiscoveryState
from mcp_server.client import MCPToolClient


def build_graph(mcp: MCPToolClient):
    nodes = build_nodes(mcp)
    g = StateGraph(DiscoveryState)

    g.add_node("router", nodes["router"])
    g.add_node("safety", nodes["safety"])
    g.add_node("clarify", nodes["clarify"])
    g.add_node("planner", nodes["planner"])
    g.add_node("retrieve", nodes["retrieve"])
    g.add_node("web_compare", nodes["web_compare"])
    g.add_node("web_fallback", nodes["web_fallback"])
    g.add_node("reconcile", nodes["reconcile"])
    g.add_node("answer", nodes["answer"])

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router", nodes["route_after_router"],
        {"safety": "safety", "clarify": "clarify", "planner": "planner"},
    )
    g.add_edge("safety", END)
    g.add_edge("clarify", END)
    g.add_edge("planner", "retrieve")
    g.add_conditional_edges(
        "retrieve", nodes["route_after_retrieve"],
        {"web_fallback": "web_fallback", "web_compare": "web_compare", "answer": "answer"},
    )
    g.add_edge("web_compare", "reconcile")
    g.add_edge("reconcile", "answer")
    g.add_edge("web_fallback", "answer")
    g.add_edge("answer", END)
    return g.compile()


_compiled_cache: dict[int, Any] = {}


def _graph_for(mcp: MCPToolClient):
    key = id(mcp)
    if key not in _compiled_cache:
        _compiled_cache.clear()          # one live client at a time
        _compiled_cache[key] = build_graph(mcp)
    return _compiled_cache[key]


async def run_discovery(
    transcript: str,
    mcp: MCPToolClient,
    *,
    search_context: dict[str, Any] | None = None,
    apply_only: bool = False,
) -> dict[str, Any]:
    """Run the full pipeline and shape the response for the frontend.

    `search_context` is the client-held search session: the constraints already
    accumulated. When present this run is a refinement — the router merges the
    new utterance onto them and the whole catalog is searched again with the
    combined intent.
    """
    graph = _graph_for(mcp)
    initial: dict[str, Any] = {"transcript": transcript, "steps": []}
    if search_context:
        prior = search_context.get("constraints") or {}
        if prior:
            initial["prior_constraints"] = prior
        if search_context.get("top_k"):
            initial["prior_top_k"] = search_context["top_k"]
        if apply_only:
            initial["apply_only"] = True
            initial["prior_needs_live"] = bool(search_context.get("needs_live"))
            initial["applied_changes"] = search_context.get("changes") or []
    final: DiscoveryState = await graph.ainvoke(initial)

    answer = final.get("answer") or {}
    picks = final.get("top_picks") or []
    comparison_table = [
        {
            "doc_id": p.get("doc_id"),
            "title": p.get("title"),
            "brand": p.get("brand"),
            "price": p.get("price"),
            "price_per_oz": p.get("price_per_oz"),
            "rating": p.get("rating"),
            "ingredients": p.get("ingredients"),
            "features": p.get("features"),
            "url": p.get("url"),
            "source": p.get("source") or "private",
            "product_url": p.get("product_url"),
            "category": p.get("category"),
            "specification": p.get("specification"),
            "technical_details": p.get("technical_details"),
            "model_number": p.get("model_number"),
            "shipping_weight": p.get("shipping_weight"),
            "product_dimensions": p.get("product_dimensions"),
            "eco_friendly": p.get("eco_friendly"),
            # Live-result fields: retailer name for "View at X", and an
            # explicit price_verified flag so the card can say "Price not
            # verified" instead of implying a price it never saw.
            "retailer": p.get("retailer"),
            "price_verified": p.get("price_verified"),
            "availability": p.get("availability"),
            # Amazon CDN URL from this product's own dataset row (never inferred).
            "image": p.get("image"),
            # Evidence-backed "why this product" data (graph/nodes.py:match_evidence).
            "match_reasons": p.get("match_reasons") or [],
            "matched_constraints": p.get("matched_constraints"),
            "supported_constraints": p.get("supported_constraints"),
        }
        for p in picks
    ]

    # Citations: private rows cite by doc_id; web rows / matched live pages
    # cite by URL — the same split the UI's CitationList renders.
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    cited_ids = answer.get("citation_doc_ids") or [p.get("doc_id") for p in picks]
    by_id = {p.get("doc_id"): p for p in picks}
    for doc_id in cited_ids:
        row = by_id.get(doc_id)
        if not row:
            continue
        if str(doc_id).startswith("web-"):
            url = row.get("url")
            if url and url not in seen_urls:
                citations.append({"type": "live", "url": url, "title": row.get("title")})
                seen_urls.add(url)
        else:
            citations.append({
                "type": "private",
                "doc_id": doc_id,
                "title": row.get("title"),
                "brand": row.get("brand"),
                "product_url": row.get("product_url"),
            })
    for match in ((final.get("reconciliation") or {}).get("matches") or {}).values():
        url = match.get("web_url")
        if url and url not in seen_urls:
            citations.append({"type": "live", "url": url, "title": match.get("web_title")})
            seen_urls.add(url)

    top_pick = by_id.get(answer.get("top_pick_doc_id")) or (picks[0] if picks else None)
    if top_pick:
        top_pick = next(
            (r for r in comparison_table if r["doc_id"] == top_pick.get("doc_id")), None
        )

    # Search pages are useful escape hatches, but are not products or evidence.
    search_links = [
        {"title": r.get("title"), "url": r.get("url")}
        for r in ((final.get("web") or {}).get("results") or [])
        if _RATING_REQUEST_RE.search(transcript) and _is_search_results_page(r)
        and r.get("url")
    ]
    generated_search_links = False
    if _RATING_REQUEST_RE.search(transcript) and not search_links:
        product = ((final.get("router") or {}).get("constraints") or {}).get("product_type")
        queries = [f"highest rated {product or 'products'}"]
        search_links = [
            {"title": f"Amazon: {query.title()}",
             "url": f"https://www.amazon.com/s?k={quote_plus(query)}"}
            for query in queries
        ]
        generated_search_links = True

    steps = list(final.get("steps", []))
    if generated_search_links:
        steps.append(step(
            "search_links",
            {"reason": "live search returned no usable search page"},
            {"links": search_links},
            label="Preparing reference links",
            detail="Generated Amazon search link — reference only, not evidence",
        ))

    return {
        "transcript": transcript,
        "steps": steps,
        # spoken_answer is what TTS reads (short); answer_detail is the fuller
        # on-screen text. The UI shows detail and speaks spoken_answer.
        "spoken_answer": answer.get("spoken_answer", ""),
        "answer_detail": answer.get("answer_detail") or answer.get("spoken_answer", ""),
        "top_pick": top_pick,
        "comparison_table": comparison_table,
        "citations": citations,
        "search_links": search_links,
        "blocked": bool(final.get("blocked")),
        "source": final.get("mode", "private"),
        # Set when the request was too broad; UI shows the single question.
        "clarify": final.get("clarify"),
        # What the router understood — drives the "What I understood" panel.
        "understood": (final.get("router") or {}).get("constraints") or {},
        "top_k": (final.get("router") or {}).get("top_k"),
        "needs_live": bool((final.get("router") or {}).get("needs_live")),
        # The search session to echo back to the client for the next refinement.
        "search_context": {
            "constraints": (final.get("router") or {}).get("constraints") or {},
            "top_k": (final.get("router") or {}).get("top_k"),
        },
        "search_query": final.get("effective_query") or transcript,
        "constraint_changes": final.get("constraint_changes") or [],
        # Catalog<->live matches, so the comparison view can show live price /
        # availability beside the catalog price without re-deriving anything.
        "reconciliation": final.get("reconciliation"),
        # Present only when a hard constraint eliminated every candidate.
        "no_match": final.get("no_match"),
        "live_unverified": final.get("live_unverified"),
        "related_online": [
            {**{k: p.get(k) for k in ("doc_id","title","price","price_verified",
                                      "retailer","url","availability","image","source",
                                      "match_reasons","matched_constraints",
                                      "supported_constraints","unverified_hard")}}
            for p in (final.get("related_online") or [])
        ],
    }
