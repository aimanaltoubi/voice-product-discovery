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

from langgraph.graph import END, StateGraph

from graph.nodes import build_nodes
from graph.state import DiscoveryState
from mcp_server.client import MCPToolClient


def build_graph(mcp: MCPToolClient):
    nodes = build_nodes(mcp)
    g = StateGraph(DiscoveryState)

    g.add_node("router", nodes["router"])
    g.add_node("safety", nodes["safety"])
    g.add_node("planner", nodes["planner"])
    g.add_node("retrieve", nodes["retrieve"])
    g.add_node("web_compare", nodes["web_compare"])
    g.add_node("web_fallback", nodes["web_fallback"])
    g.add_node("reconcile", nodes["reconcile"])
    g.add_node("answer", nodes["answer"])

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router", nodes["route_after_router"],
        {"safety": "safety", "planner": "planner"},
    )
    g.add_edge("safety", END)
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


async def run_discovery(transcript: str, mcp: MCPToolClient) -> dict[str, Any]:
    """Run the full pipeline and shape the response for the frontend."""
    graph = _graph_for(mcp)
    final: DiscoveryState = await graph.ainvoke(
        {"transcript": transcript, "steps": []}
    )

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

    return {
        "transcript": transcript,
        "steps": final.get("steps", []),
        "spoken_answer": answer.get("spoken_answer", ""),
        "top_pick": top_pick,
        "comparison_table": comparison_table,
        "citations": citations,
        "blocked": bool(final.get("blocked")),
        "source": final.get("mode", "private"),
    }
