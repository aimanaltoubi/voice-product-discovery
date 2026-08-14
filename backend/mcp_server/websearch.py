"""web.search backends. All providers normalize to the brief's schema:
    {title, url, snippet, price?, availability?}

Default is the keyless DuckDuckGo backend (`ddgs`); Serper / Brave / Tavily
are official search APIs selected via WEB_SEARCH_PROVIDER + an API key.
We only call search APIs / libraries — we never scrape result pages, which is
how this layer respects robots.txt / ToS (see docs/safety.md).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

_PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:[.,]\d{2})?)")
_AVAIL_RE = re.compile(r"\b(out of stock|in stock|currently unavailable|available|sold out)\b", re.I)


def _extract_price(text: str) -> float | None:
    m = _PRICE_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _extract_availability(text: str) -> str | None:
    m = _AVAIL_RE.search(text or "")
    return m.group(1).lower() if m else None


def _normalize(title: str, url: str, snippet: str) -> dict[str, Any]:
    blob = f"{title} {snippet}"
    out: dict[str, Any] = {"title": title, "url": url, "snippet": snippet[:400]}
    price = _extract_price(blob)
    avail = _extract_availability(blob)
    if price is not None:
        out["price"] = price
    if avail is not None:
        out["availability"] = avail
    return out


def _search_ddg(query: str, n: int) -> list[dict]:
    try:
        from ddgs import DDGS  # maintained fork
    except ImportError:  # pragma: no cover
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        raw = list(ddgs.text(query, max_results=n))
    return [_normalize(r.get("title", ""), r.get("href") or r.get("url", ""), r.get("body", ""))
            for r in raw]


def _search_serper(query: str, n: int) -> list[dict]:
    r = httpx.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": n},
        headers={"X-API-KEY": settings.SERPER_API_KEY},
        timeout=12,
    )
    r.raise_for_status()
    items = (r.json().get("organic") or [])[:n]
    return [_normalize(i.get("title", ""), i.get("link", ""), i.get("snippet", "")) for i in items]


def _search_brave(query: str, n: int) -> list[dict]:
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": n},
        headers={"X-Subscription-Token": settings.BRAVE_API_KEY, "Accept": "application/json"},
        timeout=12,
    )
    r.raise_for_status()
    items = ((r.json().get("web") or {}).get("results") or [])[:n]
    return [_normalize(i.get("title", ""), i.get("url", ""), i.get("description", "")) for i in items]


def _search_tavily(query: str, n: int) -> list[dict]:
    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": settings.TAVILY_API_KEY, "query": query, "max_results": n},
        timeout=12,
    )
    r.raise_for_status()
    items = (r.json().get("results") or [])[:n]
    return [_normalize(i.get("title", ""), i.get("url", ""), i.get("content", "")) for i in items]


_PROVIDERS = {"ddg": _search_ddg, "serper": _search_serper,
              "brave": _search_brave, "tavily": _search_tavily}


def _domain_allowed(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in settings.WEB_ALLOWED_DOMAINS)


def run_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Execute a search and apply the safety domain allowlist.

    If the allowlist filters everything out and WEB_ALLOWLIST_STRICT=false
    (default), the unfiltered results are returned with allowlist_relaxed=true
    so downstream agents can surface that fact.
    """
    provider = settings.WEB_SEARCH_PROVIDER
    fn = _PROVIDERS.get(provider)
    if fn is None:
        return {"provider": provider, "results": [],
                "error": f"unknown WEB_SEARCH_PROVIDER '{provider}'"}
    try:
        raw = fn(query, max(1, min(int(max_results), 10)))
    except Exception as e:  # network/key errors degrade gracefully (resilience)
        return {"provider": provider, "results": [], "error": f"{type(e).__name__}: {e}"}

    raw = [r for r in raw if r.get("url")]
    filtered = [r for r in raw if _domain_allowed(r["url"])]
    relaxed = False
    if not filtered and raw and not settings.WEB_ALLOWLIST_STRICT:
        filtered, relaxed = raw, True
    return {"provider": provider, "query": query, "results": filtered,
            "allowlist_relaxed": relaxed}
