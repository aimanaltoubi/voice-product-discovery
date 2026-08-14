"""FastAPI gateway — the three endpoints the React app calls.

    POST /api/transcribe  audio blob      -> {transcript, segments, ...}
    POST /api/discover    {transcript}    -> full agent payload (steps, answer, table, citations)
    POST /api/speak       {text}          -> {audio_url}  (mp3 under /media)

On startup the app launches the MCP tool server as a stdio subprocess and
performs tool discovery; the discovered catalog is visible at /api/health.
Each /api/discover run is persisted as JSONL under backend/logs/runs/.

Run from backend/:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import MEDIA_DIR, RUN_LOGS_DIR, STORAGE_DIR, settings
from graph.build import run_discovery
from mcp_server.client import MCPToolClient
from speech.asr import transcribe as asr_transcribe
from speech.tts import synthesize

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp = MCPToolClient()
    await mcp.start()
    app.state.mcp = mcp
    log.info(
        "MCP server started (stdio). Discovered tools: %s",
        [t["name"] for t in mcp.tool_catalog],
    )
    try:
        yield
    finally:
        await mcp.stop()


app = FastAPI(title="Voice Product Discovery API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

class DiscoverRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
        "asr": settings.ASR_PROVIDER,
        "tts": settings.TTS_PROVIDER,
        "embeddings": settings.EMBEDDINGS_PROVIDER,
        "catalog": _catalog_status(),
        "mcp_tools": app.state.mcp.tool_catalog,
    }


def _catalog_status() -> dict:
    """Which catalog is actually serving rag.search — real Amazon data or the
    synthetic sample. Surfaced so a demo can never silently run on sample data.
    """
    meta_path = STORAGE_DIR / "catalog_meta.json"
    if not meta_path.exists():
        return {"data_source": "none", "is_real_data": False,
                "detail": "index not built — run rag.ingest"}
    try:
        meta = json.loads(meta_path.read_text())
    except Exception as e:
        return {"data_source": "unreadable", "is_real_data": False, "detail": str(e)}
    return {
        "data_source": meta.get("data_source", "unknown"),
        "is_real_data": bool(meta.get("is_real_data")),
        "count": meta.get("count"),
        "source_file": meta.get("source_file"),
        "missing_fields": meta.get("missing_fields", []),
    }


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(await audio.read())
        tmp.close()
        result = await asr_transcribe(tmp.name)
    except Exception as e:  # surfaced to the UI's error banner
        log.exception("ASR failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}") from e
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if not result.get("transcript"):
        raise HTTPException(status_code=422, detail="No speech detected in the recording.")
    return result


@app.post("/api/discover")
async def discover(req: DiscoverRequest):
    started = time.perf_counter()
    try:
        payload = await run_discovery(req.transcript.strip(), app.state.mcp)
    except Exception as e:
        log.exception("Discovery pipeline failed")
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}") from e

    # Persist the full run for observability / grading evidence.
    run_id = uuid.uuid4().hex[:10]
    record = {
        "run_id": run_id,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "llm": f"{settings.LLM_PROVIDER}:{settings.LLM_MODEL}",
        **payload,
    }
    try:
        with (RUN_LOGS_DIR / f"{time.strftime('%Y%m%d')}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("Could not persist run log for %s", run_id)
    return payload


@app.post("/api/speak")
async def speak(req: SpeakRequest):
    try:
        filename = await synthesize(req.text)
    except Exception as e:
        log.exception("TTS failed")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {e}") from e
    return {"audio_url": f"/media/{filename}"}
