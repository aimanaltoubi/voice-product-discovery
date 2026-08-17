# Architecture

## System overview

```
┌────────────────────────────  Browser (React + Vite)  ───────────────────────────┐
│  MicRecorder → transcript → AgentTrace · ComparisonTable · CitationList · 🔊    │
└───────────────┬─────────────────────────────────────────────────────────────────┘
                │  /api/transcribe  /api/discover  /api/speak   (Vite proxy → :8000)
┌───────────────▼─────────────────────────────  FastAPI (backend/app/main.py) ────┐
│  speech/asr.py (faster-whisper | OpenAI)      speech/tts.py (edge-tts | OpenAI) │
│                                                                                 │
│  graph/build.py — LangGraph StateGraph                                          │
│   router ─┬─→ safety ────────────────────────────────────────────────→ END       │
│           ├─→ clarify ───────────────────────────────────────────────→ END       │
│           └─→ planner → retrieve ─┬─→ no_match ──────────────────┐               │
│                                   ├─→ web_compare → reconcile ──┤               │
│                                   ├─→ web_fallback ─────────────┤               │
│                                   └─────────────────────────────┴→ answerer      │
│        │                       │                     │                          │
│        └── MCP client (stdio, mcp_server/client.py) ─┘                          │
└───────────────┬─────────────────────────────────────────────────────────────────┘
                │  JSON-RPC over stdio (Model Context Protocol)
┌───────────────▼──────────────  MCP server (mcp_server/server.py) ───────────────┐
│  tools/list → discovery      web.search (TTL cache + rate limit + allowlist)    │
│                              rag.search (Chroma hybrid retrieval)               │
│  logging → backend/logs/mcp_server.jsonl                                        │
└───────────────┬──────────────────────────────┬──────────────────────────────────┘
        DuckDuckGo/Serper/Brave/Tavily   Chroma index (backend/storage/chroma)
                                          built by rag/ingest.py from the
                                          Amazon-2020 CSV / sample CSV
```

## Request lifecycle (one voice turn)

1. Browser records a WebM blob → `POST /api/transcribe` → Whisper produces
   timestamped **segments** (fragments) + joined transcript.
2. `POST /api/discover` runs the LangGraph pipeline (below); every node
   appends `{name, input, output, timestamp}` to `steps`, which the UI
   renders as the expandable Agent Trace.
3. `POST /api/speak` synthesizes the 20–30-word `spoken_answer` to an mp3
   under `/media/`, which the browser auto-plays.

## Graph nodes (backend/graph/nodes.py)

| Node | Type | Prompt file | What it does |
|---|---|---|---|
| `router` | LLM + code | `prompts/router.md` + few-shots | extracts task/constraints; code grounds budget, material, live intent and the union of LLM + deterministic safety flags in the utterance |
| `safety` | deterministic | — | hard block with flag-specific refusal for verified mixing, ingestion or harmful intent |
| `clarify` | deterministic | — | asks one question when a product type is present but there is not enough information to rank responsibly |
| `planner` | LLM + code | `prompts/planner.md` | picks MCP `sources`, retrieval filters, comparison criteria; **code re-enforces the rubric** (rag.search always; web.search iff `needs_live`) and back-fills filters from router constraints |
| `retrieve` (`rag.search` step) | MCP tool + LLM | `prompts/reranker.md` | expands categories to their catalog family, applies the relevance floor inside a logged relaxation ladder, then reranks up to 5 results; code validates ids and logs score-based top-ups |
| `no_match` | deterministic | — | reports that a hard budget cannot be met and surfaces the closest grounded alternatives without an unnecessary web call |
| `web_compare` (`web.search` step) | MCP tool | — | live results for the top-ranked pick only when the user asked for current price/availability |
| `web_fallback` (`web.search` step) | MCP tool | — | when the private catalog has zero matches, answer from live web rows (`web-1..n`) instead |
| `reconcile` | deterministic | — | matches the top catalog pick to web titles using ≥2 distinctive shared tokens, or 1 distinctive token with ≥0.6 containment; flags price deltas >15% |
| `answerer` | LLM + code | `prompts/answerer.md` | 20–30-word grounded spoken answer with no forced follow-up; **critic in code** validates citations, top-pick ids, and stated prices, then appends omitted discrepancy notices |

## Where "agentic" decisions happen

- Tool choice is decided per-request by the planner (LLM) and *verified* by
  deterministic rubric code — the step log shows both the LLM plan and any
  `enforced_rules`.
- Conditional edges: safety and clarification short-circuits, hard-budget
  no-match response, zero-candidate web fallback, and needs-live comparison.
- The LLM proposes; code verifies (rerank subset check, answer grounding
  check, discrepancy mention check). Failures are visible in the step log
  (`critic_notes`, `dropped_unknown_doc_ids`).
- The catalog has no ratings or reviews, so those requests are logged as
  unsupported privately and routed to live search; generated search links are
  labeled as reference-only and logged separately from evidence.
- Deterministic guards intentionally use small vocabulary lists for material,
  safety, audience, freshness and reconciliation checks. Outside those lists,
  safety unions the Router's LLM flags with exact code matches; retrieval
  degrades by omitting an unsupported filter rather than inventing evidence.

## MCP specifics

- Transport: stdio subprocess (`python -m mcp_server.server`), launched by
  the FastAPI lifespan; `MCP_TRANSPORT=streamable-http` also supported.
- Discovery: the client calls `tools/list` at startup; discovered names +
  JSON schemas are exposed at `GET /api/health` (grading evidence).
- The graph never imports retrieval/search functions directly — every tool
  interaction is a real MCP `tools/call`.

## Model-agnostic LLM layer

`graph/llm.py` instantiates the chat model through LangChain's
`init_chat_model` from `LLM_PROVIDER`/`LLM_MODEL`; all nodes use
`with_structured_output` against the Pydantic schemas in `graph/state.py`.
`LLM_PROVIDER=mock` swaps in deterministic heuristics (clearly labeled) so
the whole system runs keyless — used by `scripts/smoke_test.py` and CI.

## Observability

- Per-run JSONL: `backend/logs/runs/YYYYMMDD.jsonl` (full payload + timing).
- Per-tool-call JSONL: `backend/logs/mcp_server.jsonl` (timestamps, truncated
  request/response, source URLs, cache hits, durations). No secrets logged.
