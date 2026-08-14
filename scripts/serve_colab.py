"""Single-port launcher for Colab / simple hosting.

Serves the *built* frontend (frontend/dist) from the FastAPI app itself, so
one uvicorn process on :8000 handles the UI, /api/*, and /media/* — ideal
behind a single HTTPS tunnel (Cloudflare/ngrok). API routes are registered
before the static mount, so they take precedence.

Usage (from repo root, after `npx vite build` in frontend/):
    python scripts/serve_colab.py            # http://localhost:8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
DIST = REPO_ROOT / "frontend" / "dist"

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # relative paths (logs, storage) behave as documented

if not DIST.exists():
    raise SystemExit(
        "frontend/dist not found — build the UI first:\n"
        "  cd frontend && npm install && npx vite build"
    )

from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402  (registers /api/* and /media first)

app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    # MCP stdio requires the stock asyncio loop (uvloop breaks it).
    uvicorn.run(app, host="0.0.0.0", port=port, loop="asyncio")
