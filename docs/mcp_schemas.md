# MCP tool contracts

Server: `backend/mcp_server/server.py` (name: `product-discovery-tools`).
Transport: stdio (default) or streamable HTTP (`MCP_TRANSPORT=streamable-http`).
Discovery: standard `tools/list`; the discovered catalog (names, descriptions,
JSON input schemas) is also surfaced at `GET /api/health`.

Exactly **two** tools are exposed, as the brief requires.

---

## `web.search`

Live web results for price/availability comparison.

**Input schema** (auto-derived from the typed signature):

```json
{
  "type": "object",
  "properties": {
    "query":       {"type": "string"},
    "max_results": {"type": "integer", "default": 5}
  },
  "required": ["query"]
}
```

**Output**:

```json
{
  "provider": "ddg",
  "query": "EcoShine stainless steel cleaner price",
  "results": [
    {
      "title": "EcoShine Stainless Steel Cleaner 16oz - Walmart.com",
      "url": "https://www.walmart.com/ip/...",
      "snippet": "…$11.97 … In stock …",
      "price": 11.97,          // optional — parsed when present
      "availability": "in stock"  // optional — parsed when present
    }
  ],
  "cached": false,             // true when served from the TTL cache
  "allowlist_relaxed": false   // true if the domain allowlist was relaxed
}
```

**Behavior guarantees**

- TTL cache: `WEB_CACHE_TTL_SECONDS`, clamped to the spec's **60–300 s**.
- Rate limit: `WEB_RATE_LIMIT_PER_MIN` (sliding 60 s window). Over-limit
  calls return `{"error": "rate_limited", "retry_after_seconds": …}`.
- Domain allowlist (retail/review sites) with suffix match on the hostname;
  strict mode (`WEB_ALLOWLIST_STRICT=true`) drops off-list results even if
  that leaves zero.
- Network failures degrade to `{"results": [], "error": …}` — the graph
  continues instead of crashing.
- Provider is env-selected: `ddg` (keyless default) | `serper` | `brave` |
  `tavily`.

---

## `rag.search`

Hybrid retrieval (vector similarity + metadata filters) over the private
catalog index.

**Input schema**:

```json
{
  "type": "object",
  "properties": {
    "query":        {"type": "string"},
    "max_price":    {"type": "number"},
    "category":     {"type": "string"},
    "material":     {"type": "string"},
    "eco_friendly": {"type": "boolean"},
    "top_k":        {"type": "integer", "default": 8}
  },
  "required": ["query"]
}
```

**Output**:

```json
{
  "results": [
    {
      "sku": "SAMPLE-001",
      "doc_id": "SAMPLE-001",
      "title": "EcoShine Steel-Safe Stainless Steel Cleaner Spray 16 oz",
      "brand": "EcoShine",
      "category": "Household Cleaning > Stainless Steel",
      "price": 12.49,
      "rating": 4.6,
      "ingredients": "Plant-based surfactants, citric acid…",
      "features": "Streak-free shine; plant-based…",
      "eco_friendly": true,
      "size_oz": 16.0,
      "price_per_oz": 0.7806,
      "score": 0.83
    }
  ],
  "resolved_category": "Household Cleaning > Stainless Steel",
  "relaxations": [],
  "filters_applied": {"max_price": 15.0, "eco_friendly": true, "...": "..."}
}
```

Matches the brief's required fields `{sku, title, price, rating, brand?,
ingredients?, doc_id}` and adds the value-normalization field
(`price_per_oz`) plus retrieval metadata. `doc_id` is the citation key used
across the step log, comparison table, and citations list.

**Behavior guarantees**

- Filters map to Chroma `where` clauses (`$lte` price, `$eq`
  category/eco) plus a `where_document` contains-clause for material.
- If a filter combination yields nothing, filters are **relaxed in a
  logged ladder** (drop material → drop category → no filters with a
  Python-side price re-check); the `relaxations` list records each step.
- Rate limit: `RAG_RATE_LIMIT_PER_MIN`.
- If the index hasn't been built, returns `{"error": "index_not_built"}` —
  the graph then falls back to `web.search` and the step log says why.

---

## Logging

Every call to either tool appends one JSONL line to
`backend/logs/mcp_server.jsonl`:

```json
{"timestamp": "2026-08-14T01:58:03+0000", "tool": "rag.search",
 "request": {"query": "…", "max_price": 15.0},
 "response": {"results": ["…truncated…"]},
 "source_urls": [], "duration_ms": 42.1, "cached": false}
```

Requests/responses are truncated; API keys and other secrets are never
written (they never enter the tool payloads in the first place).
