# Voice Product Discovery — Agentic Voice-to-Voice AI Assistant

Speak a product request → **Whisper** transcribes it → a **LangGraph**
multi-agent pipeline (router → planner → retriever → answerer/critic) plans
which **MCP tools** to call — `rag.search` over a private Amazon-2020 catalog
index and `web.search` for live price/availability — reconciles conflicts,
and answers with a **~15-second spoken summary** plus on-screen citations, a
comparison table, and a full agent step log.

Built as a self-contained repo: React (Vite) frontend + Python FastAPI
backend. No external platform dependencies — everything the agent does runs
from this repository.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/aimanaltoubi/voice-product-discovery/blob/main/colab_launch.ipynb)
*One-click cloud demo — no local setup. (After pushing, replace `aimanaltoubi/voice-product-discovery` in this badge link and inside `colab_launch.ipynb`.)*

**Try saying:**
- *“Find me an eco-friendly stainless-steel cleaner under fifteen dollars.”* → private-catalog path
- *“What’s the current price of a glass cleaner right now?”* → adds live `web.search` + reconciliation
- *“Can I mix bleach and ammonia for a stronger cleaner?”* → safety gate blocks with a safe spoken refusal

---

## Architecture (short version)

```
Browser (React) ── /api/transcribe ─▶ Whisper ASR (faster-whisper | OpenAI)
      │                /api/discover ─▶ LangGraph:
      │                                router → [safety] → planner
      │                                  → retrieve(rag.search + rerank)
      │                                  → [web_compare → reconcile | web_fallback]
      │                                  → answerer/critic
      │                /api/speak ────▶ TTS (edge-tts | OpenAI) → mp3
      │                                        │
      └── step log · table · citations   MCP client ── stdio JSON-RPC ──▶ MCP server
                                                        web.search (cache+ratelimit+allowlist)
                                                        rag.search (Chroma hybrid retrieval)
```

Full detail: [`docs/architecture.md`](docs/architecture.md) ·
tool contracts: [`docs/mcp_schemas.md`](docs/mcp_schemas.md) ·
guardrails: [`docs/safety.md`](docs/safety.md) ·
data pipeline: [`data/README.md`](data/README.md) ·
prompts + node mapping: [`prompts/README.md`](prompts/README.md)

## Quickstart

Prereqs: Python 3.11+ (3.12 tested), Node 18+, a microphone-capable browser.

```bash
# 1) backend deps
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) configuration
cp ../.env.example ../.env     # then edit: set OPENAI_API_KEY (LLM_PROVIDER=openai)

# 3) build the private-catalog index (bundled synthetic sample, 24 products)
python -m rag.ingest --sample
#    …or the real Kaggle Amazon-2020 slice (see data/README.md):
# python -m rag.ingest --csv ../data/raw/<kaggle-file>.csv --category "Household" --limit 3000

# 4) frontend deps
cd ../frontend && npm install

# 5) run both (from repo root)
cd .. && PY=backend/.venv/bin/python ./run.sh
#    or manually, in two terminals:
#    (backend)  cd backend && python -m uvicorn app.main:app --port 8000 --loop asyncio
#    (frontend) cd frontend && npm run dev
```

Open http://localhost:5173, allow the microphone, and talk. First
transcription downloads the local Whisper model once (~150 MB for `base`);
set `ASR_PROVIDER=openai` to skip local models entirely.

> **Note the `--loop asyncio` flag** (run.sh passes it for you): the MCP
> stdio transport does not work under uvloop, so the backend must run on the
> stock asyncio event loop.

### Keyless offline mode + smoke test

The whole pipeline runs with **zero API keys and zero network**:

```bash
LLM_PROVIDER=mock EMBEDDINGS_PROVIDER=hash python scripts/smoke_test.py
```

This ingests the sample catalog, boots the MCP server over stdio, verifies
tool discovery, and runs the three demo scenarios end-to-end with
assertions. `mock` is a deterministic heuristic (clearly labeled in the step
log) — use a real `LLM_PROVIDER` for actual quality; `hash` embeddings are
test-only.

### Run in Colab (zero local setup)

[`colab_launch.ipynb`](colab_launch.ipynb) launches everything on a free
Colab VM and prints a public **HTTPS** URL (Cloudflare quick tunnel), so the
browser microphone works:

1. Open the notebook via the badge above (edit the two `aimanaltoubi/voice-product-discovery`
   placeholders once — in the badge link and in the notebook's `REPO_URL` —
   right after you push).
2. *(Optional but recommended)* Colab sidebar → 🔑 **Secrets** → add
   `OPENAI_API_KEY` → enable **Notebook access**. The key stays in your
   Google account; anyone else running the notebook uses their own secrets —
   or no key at all, in which case it auto-falls back to the keyless mock mode.
3. **Runtime → Run all**, wait ~4–6 min, open the printed
   `https://….trycloudflare.com` URL.

The Colab path serves the built UI and the API from **one** port via
`scripts/serve_colab.py` (also handy for any single-port hosting). The URL
is ephemeral and lives only while the notebook runs — a demo runtime, not
hosting.

## Configuration

Everything is env-driven — see [`.env.example`](.env.example). Highlights:

| Variable | Options (default first) | Purpose |
|---|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` | `openai:gpt-4o-mini`, anthropic, google_genai, ollama, mock | model-agnostic LLM via LangChain `init_chat_model` |
| `EMBEDDINGS_PROVIDER` | `local` (ONNX MiniLM), openai, hash | RAG index embeddings |
| `ASR_PROVIDER` | `local` (faster-whisper), openai | Whisper transcription |
| `TTS_PROVIDER` | `edge` (keyless), openai | speech synthesis |
| `WEB_SEARCH_PROVIDER` | `ddg` (keyless), serper, brave, tavily | backend for `web.search` |
| `WEB_CACHE_TTL_SECONDS` | 180 (clamped 60–300) | `web.search` response cache |
| `WEB_ALLOWED_DOMAINS` / `WEB_ALLOWLIST_STRICT` | retail/review list | domain allowlist |

## Repository layout

```
frontend/                React + Vite UI (mic, transcript, step log, table, citations, audio)
backend/
  app/                   FastAPI gateway (/api/transcribe, /api/discover, /api/speak, /api/health)
  graph/                 LangGraph: state schemas, nodes, wiring, model-agnostic LLM layer
  mcp_server/            MCP server (web.search, rag.search) + stdio client
  rag/                   ingest (CSV → parquet + Chroma), embedders, hybrid retrieval
  speech/                Whisper ASR + TTS
  logs/ · media/ · storage/   runtime artifacts (git-ignored)
prompts/                 ALL runtime prompts + node mapping (Prompt Disclosure)
data/                    sample catalog, Kaggle instructions, processed parquet (ignored)
docs/                    architecture, MCP schemas, safety
scripts/smoke_test.py    keyless offline end-to-end test
run.sh · .env.example
```


| item | Where it’s satisfied |
|---|---|
| **Functionality** (end-to-end voice flow; multi-agent routing; citations shown) | Mic → `/api/transcribe` (Whisper) → `/api/discover` (LangGraph, `backend/graph/`) → `/api/speak` (TTS auto-plays). Router/planner/retriever/answerer + safety and reconcile nodes with conditional edges (`graph/build.py`). Citations rendered with `doc_id` (private) and URLs (live) — `CitationList.jsx`, built in `graph/build.py`. |
| **Agentic RAG Quality** (accurate retrieval; grounded answers; sensible hybrid use) | Embeddings over title+features+review snippets (`rag/ingest.py`) in Chroma; **hybrid** vector + metadata filters (price/category/material/eco) with a logged relaxation ladder (`rag/retrieval.py`); LLM reranker validated in code; answerer grounded to retrieved rows with citation/top-pick validation and price-per-oz normalization. |
| **MCP Server** (two tools working; discovery & schemas; caching/logging) | Exactly `web.search` + `rag.search` (`mcp_server/server.py`), stdio (or HTTP) transport, standard `tools/list` discovery with JSON schemas (visible at `GET /api/health`), TTL cache 60–300 s + per-tool rate limits, JSONL logging with timestamps and source URLs. Contracts: `docs/mcp_schemas.md`. |
| **Planning & Tool Use** (clear plans; conflict handling; reconciliation) | Planner LLM output + deterministic rubric enforcement (“prefer rag.search; add web.search only for current/latest/availability”) with `enforced_rules` in the step log (`nodes.py::planner_node`); reconcile node matches catalog↔web by normalized title/brand similarity (SKU-less web rows), flags >15 % price deltas and availability; critic forces discrepancy mention into the spoken answer. |
| **UI/UX** (clean app; transcript; comparison table; audio playback) | React app: `MicRecorder` (record/upload), editable transcript, `AgentStepLog` (every node’s input/output/timestamp), `ComparisonTable` (price, $/oz, rating, ingredients, top-pick highlight), `CitationList`, auto TTS playback with replay. |
| **Prompt Disclosure** | [`prompts/`](prompts/) is the **runtime source** (loaded by `graph/prompts.py`, not copies): system prompt, router + few-shots, planner (contains the planner rubric verbatim), reranker, answerer/critic, and a prompt→node→schema mapping table in `prompts/README.md`. Prompts and provider names are logged per step. |

required: fragment-based ASR
(timestamped Whisper segments, `speech/asr.py`) and fragment-based TTS with
a ~40-word spoken summary ending in the *“most affordable or highest
rated?”* follow-up (`prompts/answerer.md`, `speech/tts.py`); model-agnostic
LLM via env (`graph/llm.py`); `.env.example` + `run.sh`; safety
(domain allowlist, no unsafe chemical advice, no secret logging —
`docs/safety.md`); Amazon-2020 ingestion with parquet outputs
(`data/README.md`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/api/discover` hangs forever | You’re on uvloop. Run uvicorn with `--loop asyncio` (run.sh does). Requirements deliberately install plain `uvicorn`, not `uvicorn[standard]`. |
| `rag.search` returns `index_not_built` | Run `python -m rag.ingest --sample` (from `backend/`). |
| “index was built with embedder X but current is Y” | Re-run ingest after changing `EMBEDDINGS_PROVIDER` — the index refuses mismatched embedders on purpose. |
| First transcription is slow / downloads | faster-whisper fetches the model once; or set `ASR_PROVIDER=openai`. |
| TTS error mentioning `speech.platform.bing.com` | Your network blocks Edge TTS; set `TTS_PROVIDER=openai`. |
| `web.search` returns zero results | DuckDuckGo throttling or offline; results degrade gracefully (the step log shows the error). Configure Serper/Brave/Tavily keys for reliability. |
| Mic button does nothing | Browsers require `localhost` or HTTPS for `getUserMedia`; use the Vite URL, not a LAN IP. |
