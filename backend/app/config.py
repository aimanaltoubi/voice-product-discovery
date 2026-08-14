"""Central configuration. Every provider choice (LLM, embeddings, ASR, TTS,
web search) is driven purely by environment variables, satisfying the brief's
"Model-Agnostic Requirement": swap providers without touching orchestration code.

Load order: process env > repo-root .env (via python-dotenv).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
DATA_DIR = REPO_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
STORAGE_DIR = BACKEND_DIR / "storage"
CHROMA_DIR = STORAGE_DIR / "chroma"
MEDIA_DIR = BACKEND_DIR / "media"
LOGS_DIR = BACKEND_DIR / "logs"
RUN_LOGS_DIR = LOGS_DIR / "runs"

load_dotenv(REPO_ROOT / ".env")

# Silence Chroma telemetry (safety note: nothing leaves the machine).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class Settings:
    # ---- LLM (Router / Planner / Reranker / Answerer) ----
    # Providers: openai | anthropic | google_genai | ollama | mock
    # "mock" is a deterministic, keyless heuristic mode used for offline demos
    # and CI smoke tests. It is NOT an LLM and is clearly labeled in logs.
    LLM_PROVIDER: str = _get("LLM_PROVIDER", "openai").lower()
    LLM_MODEL: str = _get("LLM_MODEL", "gpt-4o-mini")

    # ---- Embeddings for the private-catalog index ----
    # local  = all-MiniLM-L6-v2 (ONNX, downloads once, runs offline)
    # openai = text-embedding-3-small (needs OPENAI_API_KEY)
    # hash   = deterministic pseudo-embeddings, TEST/CI ONLY (not semantic)
    EMBEDDINGS_PROVIDER: str = _get("EMBEDDINGS_PROVIDER", "local").lower()
    OPENAI_EMBEDDING_MODEL: str = _get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    # ---- ASR ----  local = faster-whisper (Whisper, offline) | openai = Whisper API
    ASR_PROVIDER: str = _get("ASR_PROVIDER", "local").lower()
    WHISPER_MODEL_SIZE: str = _get("WHISPER_MODEL_SIZE", "base")
    OPENAI_ASR_MODEL: str = _get("OPENAI_ASR_MODEL", "whisper-1")

    # ---- TTS ----  edge = edge-tts (free neural voices) | openai = OpenAI TTS
    TTS_PROVIDER: str = _get("TTS_PROVIDER", "edge").lower()
    EDGE_TTS_VOICE: str = _get("EDGE_TTS_VOICE", "en-US-AriaNeural")
    OPENAI_TTS_MODEL: str = _get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    OPENAI_TTS_VOICE: str = _get("OPENAI_TTS_VOICE", "alloy")

    # ---- web.search MCP tool ----
    # ddg (keyless default) | serper | brave | tavily
    WEB_SEARCH_PROVIDER: str = _get("WEB_SEARCH_PROVIDER", "ddg").lower()
    SERPER_API_KEY: str = _get("SERPER_API_KEY")
    BRAVE_API_KEY: str = _get("BRAVE_API_KEY")
    TAVILY_API_KEY: str = _get("TAVILY_API_KEY")

    # Spec: "rate-limit and cache (TTL 60-300s)" -> clamped to the spec range.
    WEB_CACHE_TTL_SECONDS: int = _clamp(_get_int("WEB_CACHE_TTL_SECONDS", 180), 60, 300)
    WEB_RATE_LIMIT_PER_MIN: int = _get_int("WEB_RATE_LIMIT_PER_MIN", 10)
    RAG_RATE_LIMIT_PER_MIN: int = _get_int("RAG_RATE_LIMIT_PER_MIN", 60)

    # Safety: domain allowlist for live results. If STRICT=false and filtering
    # empties the result set, the unfiltered set is returned with a
    # `allowlist_relaxed` flag so the graph can surface it (conflict handling).
    WEB_ALLOWED_DOMAINS: list[str] = [
        d.strip().lower()
        for d in _get(
            "WEB_ALLOWED_DOMAINS",
            "amazon.com,walmart.com,target.com,homedepot.com,lowes.com,"
            "costco.com,kroger.com,instacart.com,thespruce.com,goodhousekeeping.com,"
            "nytimes.com,wirecutter.com,consumerreports.org,ewg.org,grove.co",
        ).split(",")
        if d.strip()
    ]
    WEB_ALLOWLIST_STRICT: bool = _get("WEB_ALLOWLIST_STRICT", "false").lower() == "true"

    # ---- Retrieval ----
    RAG_TOP_K: int = _get_int("RAG_TOP_K", 8)
    CHROMA_COLLECTION: str = _get("CHROMA_COLLECTION", "products")

    # ---- Frontend origin (CORS, only needed if you bypass the Vite proxy) ----
    FRONTEND_ORIGIN: str = _get("FRONTEND_ORIGIN", "http://localhost:5173")


settings = Settings()

for _d in (STORAGE_DIR, MEDIA_DIR, LOGS_DIR, RUN_LOGS_DIR, PROCESSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)
