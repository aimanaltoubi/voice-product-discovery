"""Whisper ASR — fragment-based transcription.

Providers (env ASR_PROVIDER):
    local   -> faster-whisper running on CPU (default; model downloads on
               first use to the standard HF cache). WHISPER_MODEL_SIZE picks
               tiny/base/small/... — "base" is a good latency/quality tradeoff.
    openai  -> OpenAI Whisper API (whisper-1), needs OPENAI_API_KEY.

Both return the same shape:
    {"transcript": str,
     "segments": [{"start": float, "end": float, "text": str}, ...],
     "language": str | None,
     "provider": str}

The `segments` list is the "fragment-based" part of the brief: audio is
decoded into timestamped fragments which are then joined for the pipeline.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from app.config import settings


@lru_cache(maxsize=1)
def _local_model():
    from faster_whisper import WhisperModel

    return WhisperModel(settings.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")


def _transcribe_local_sync(path: str) -> dict:
    model = _local_model()
    segments_iter, info = model.transcribe(path, vad_filter=True)
    segments = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments_iter
    ]
    transcript = " ".join(s["text"] for s in segments).strip()
    return {
        "transcript": transcript,
        "segments": segments,
        "language": getattr(info, "language", None),
        "provider": f"local:faster-whisper-{settings.WHISPER_MODEL_SIZE}",
    }


async def _transcribe_openai(path: str) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    with open(path, "rb") as f:
        try:
            resp = await client.audio.transcriptions.create(
                model=settings.OPENAI_ASR_MODEL,
                file=f,
                response_format="verbose_json",
            )
            segments = [
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                for s in (resp.segments or [])
            ]
            return {
                "transcript": (resp.text or "").strip(),
                "segments": segments,
                "language": getattr(resp, "language", None),
                "provider": f"openai:{settings.OPENAI_ASR_MODEL}",
            }
        except Exception:
            # Some models only support plain-text responses; retry simply.
            f.seek(0)
            resp = await client.audio.transcriptions.create(
                model=settings.OPENAI_ASR_MODEL, file=f, response_format="text"
            )
            text = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
            return {
                "transcript": text.strip(),
                "segments": [],
                "language": None,
                "provider": f"openai:{settings.OPENAI_ASR_MODEL}",
            }


async def transcribe(path: str | Path) -> dict:
    path = str(path)
    if settings.ASR_PROVIDER == "openai":
        return await _transcribe_openai(path)
    # faster-whisper is blocking; keep the event loop responsive.
    return await asyncio.to_thread(_transcribe_local_sync, path)
