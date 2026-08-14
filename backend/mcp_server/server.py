"""MCP server exposing exactly the two tools the brief requires.

    web.search  -> live web results  {title, url, snippet, price?, availability?}
    rag.search  -> private catalog   {sku, title, price, rating, brand?, ingredients?, doc_id}

- Tool discovery: names + JSON input schemas are derived from the typed
  signatures below and served via the standard MCP `tools/list` handshake.
- Transport: stdio by default (`python -m mcp_server.server`), or streamable
  HTTP via `MCP_TRANSPORT=streamable-http`.
- web.search is cached (TTL clamped to the spec's 60-300 s) and both tools are
  rate-limited (token window per tool).
- Every request/response is logged with a timestamp and source URLs to
  backend/logs/mcp_server.jsonl. No secrets are ever logged.

IMPORTANT for stdio transport: never print() to stdout in this module —
stdout carries the JSON-RPC stream. Diagnostics go to the log file / stderr.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Any

try:  # mcp SDK >= 2.0 renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp SDK 1.x
    from mcp.server.fastmcp import FastMCP

from app.config import LOGS_DIR, settings

mcp = FastMCP("product-discovery-tools")

_LOG_PATH = LOGS_DIR / "mcp_server.jsonl"


def _truncate(obj: Any, limit: int = 500) -> Any:
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + "…"
    if isinstance(obj, list):
        return [_truncate(v, limit) for v in obj[:12]]
    if isinstance(obj, dict):
        return {k: _truncate(v, limit) for k, v in obj.items()}
    return obj


def _log(tool: str, request: dict, response: dict, started: float) -> None:
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": tool,
        "request": _truncate(request),
        "response": _truncate(response),
        "source_urls": [r.get("url") for r in response.get("results", []) if isinstance(r, dict) and r.get("url")],
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "cached": response.get("cached", False),
    }
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---- TTL cache (spec: web.search "rate-limit and cache (TTL 60-300s)") ----
_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str) -> dict | None:
    hit = _cache.get(key)
    if not hit:
        return None
    expires, value = hit
    if time.time() > expires:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: dict) -> None:
    _cache[key] = (time.time() + settings.WEB_CACHE_TTL_SECONDS, value)


# ---- sliding-window rate limiter, per tool ----
_windows: dict[str, deque[float]] = {"web.search": deque(), "rag.search": deque()}
_LIMITS = {"web.search": settings.WEB_RATE_LIMIT_PER_MIN,
           "rag.search": settings.RAG_RATE_LIMIT_PER_MIN}


def _rate_limited(tool: str) -> float | None:
    """Return seconds-until-retry if over the per-minute limit, else None."""
    now = time.time()
    win = _windows[tool]
    while win and now - win[0] > 60:
        win.popleft()
    if len(win) >= _LIMITS[tool]:
        return round(60 - (now - win[0]), 1)
    win.append(now)
    return None


@mcp.tool(
    name="web.search",
    description=(
        "Live web search for product price/availability comparisons. Returns "
        "{provider, query, results: [{title, url, snippet, price?, availability?}]}. "
        "Responses are cached (TTL 60-300s) and rate-limited."
    ),
)
def web_search(query: str, max_results: int = 5) -> dict:
    started = time.perf_counter()
    req = {"query": query, "max_results": max_results}
    retry = _rate_limited("web.search")
    if retry is not None:
        resp = {"error": "rate_limited", "retry_after_seconds": retry, "results": []}
        _log("web.search", req, resp, started)
        return resp
    key = f"web:{query}:{max_results}"
    cached = _cache_get(key)
    if cached is not None:
        resp = {**cached, "cached": True}
        _log("web.search", req, resp, started)
        return resp
    from mcp_server.websearch import run_web_search

    resp = run_web_search(query, max_results)
    if not resp.get("error"):
        _cache_set(key, resp)
    _log("web.search", req, resp, started)
    return resp


@mcp.tool(
    name="rag.search",
    description=(
        "Hybrid retrieval (vector similarity + metadata filters) over the private "
        "Amazon-2020 product catalog. Returns {results: [{sku, doc_id, title, brand, "
        "price, rating, ingredients, features, price_per_oz, score}], relaxations, "
        "resolved_category}. doc_id values are the citation keys."
    ),
)
def rag_search(
    query: str,
    max_price: float | None = None,
    category: str | None = None,
    material: str | None = None,
    eco_friendly: bool | None = None,
    top_k: int = 8,
) -> dict:
    started = time.perf_counter()
    req = {"query": query, "max_price": max_price, "category": category,
           "material": material, "eco_friendly": eco_friendly, "top_k": top_k}
    retry = _rate_limited("rag.search")
    if retry is not None:
        resp = {"error": "rate_limited", "retry_after_seconds": retry, "results": []}
        _log("rag.search", req, resp, started)
        return resp
    try:
        from rag.retrieval import IndexNotBuilt, hybrid_search

        resp = hybrid_search(
            query, max_price=max_price, category=category,
            material=material, eco_friendly=eco_friendly, top_k=top_k,
        )
    except Exception as e:
        name = "index_not_built" if e.__class__.__name__ == "IndexNotBuilt" else type(e).__name__
        resp = {"error": name, "detail": str(e), "results": []}
    _log("rag.search", req, resp, started)
    return resp


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
