"""Text-to-speech — fragment-based synthesis to an mp3 the browser plays.

Providers (env TTS_PROVIDER):
    edge    -> Microsoft Edge neural voices via `edge-tts` (default, keyless).
               Audio arrives as a stream of chunks ("fragments") which are
               written sequentially to one mp3.
    openai  -> OpenAI TTS (gpt-4o-mini-tts by default), needs OPENAI_API_KEY.

Returns the generated filename inside MEDIA_DIR; the API layer exposes it
as /media/<filename>. The spoken text is capped — the answerer already
targets ~40 words (~15 s), this is just a hard stop.
"""

from __future__ import annotations

import uuid

from app.config import MEDIA_DIR, settings

MAX_TTS_CHARS = 600


async def synthesize(text: str) -> str:
    text = (text or "").strip()[:MAX_TTS_CHARS]
    if not text:
        raise ValueError("No text to synthesize")
    filename = f"tts_{uuid.uuid4().hex[:12]}.mp3"
    out_path = MEDIA_DIR / filename

    if settings.TTS_PROVIDER == "openai":
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        async with client.audio.speech.with_streaming_response.create(
            model=settings.OPENAI_TTS_MODEL,
            voice=settings.OPENAI_TTS_VOICE,
            input=text,
        ) as resp:
            await resp.stream_to_file(out_path)
        return filename

    import edge_tts

    communicate = edge_tts.Communicate(text, settings.EDGE_TTS_VOICE)
    await communicate.save(str(out_path))
    return filename
