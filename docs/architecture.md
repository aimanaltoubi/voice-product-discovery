# Architecture

## System overview

```
┌────────────────────────────  Browser (React + Vite)  ───────────────────────────┐
│  MicRecorder → transcript → AgentStepLog · ComparisonTable · CitationList · 🔊  │
└───────────────┬─────────────────────────────────────────────────────────────────┘
                │  /api/transcribe  /api/discover  /api/speak   (Vite proxy → :8000)
┌───────────────▼─────────────────────────────  FastAPI (backend/app/main.py) ────┐
│  speech/asr.py (faster-whisper | OpenAI)      speech/tts.py (edge-tts | OpenAI) │
│                                                                                 │
│  graph/build.py — LangGraph StateGraph                                          │
│   router → [safety] → planner → retrieve ──┬─→ web_compare → reconcile ─┐       │
│                                            ├─→ web_fallback ────────────┤       │
│                                            └────────────────────────────┴→ answerer
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
   renders verbatim as the agent step log.
3. `POST /api/speak` synthesizes the ~40-word `spoken_answer` to an mp3
   under `/media/`, which the browser auto-plays.

## Graph nodes (backend/graph/nodes.py)

| Node | Type | Prompt file | What it does |
|---|---|---|---|
| `router` | LLM (structured) | `prompts/router.md` + few-shots | task, constraints (budget/material/brand/category/eco), `safety_flags`, `needs_live` |
| `safety` | deterministic | — | hard block + safe spoken refusal when `safety_flags` non-empty |
| `planner` | LLM + code | `prompts/planner.md` | picks MCP `sources`, retrieval filters, comparison criteria; **code re-enforces the rubric** (rag.search always; web.search iff `needs_live`) and back-fills filters from router constraints |
| `retrieve` (`rag.search` step) | MCP tool + LLM | `prompts/reranker.md` | calls `rag.search` over MCP → hybrid candidates; LLM reranks; code validates ranked ids ⊆ candidates and tops up to 3 by score |
| `web_compare` (`web.search` step) | MCP tool | — | live results for the top pick when the user asked for current price/availability |
| `web_fallback` (`web.search` step) | MCP tool | — | when the private catalog has zero matches, answer from live web rows (`web-1..n`) instead |
| `reconcile` | deterministic | — | matches catalog picks ↔ web titles by normalized title+brand similarity (≥ 0.45, SKU-less web rows); flags price deltas > 15% and availability notes |
| `answerer` | LLM + code | `prompts/answerer.md` | ~40-word grounded spoken answer ending with the affordable-vs-highest-rated follow-up; **critic in code**: citations ∩ known rows, top-pick id validated, discrepancy notice force-appended if the LLM omitted it |

## Where "agentic" decisions happen

- Tool choice is decided per-request by the planner (LLM) and *verified* by
  deterministic rubric code — the step log shows both the LLM plan and any
  `enforced_rules`.
- Conditional edges: safety short-circuit, zero-candidate web fallback,
  needs_live compare branch.
- The LLM proposes; code verifies (rerank subset check, answer grounding
  check, discrepancy mention check). Failures are visible in the step log
  (`critic_notes`, `dropped_unknown_doc_ids`).

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
