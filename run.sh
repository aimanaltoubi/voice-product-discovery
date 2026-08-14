#!/usr/bin/env bash
# Starts the FastAPI backend and the Vite dev server together.
# Prereqs: backend deps installed (see README), frontend `npm install` done,
# and the index built once: (cd backend && python -m rag.ingest --sample)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PY:-python}   # set PY=.venv/bin/python if you use a venv at repo root

echo "[run] starting backend on :8000 (stock asyncio loop — required for MCP stdio)"
(
  cd backend
  exec $PY -m uvicorn app.main:app --port 8000 --loop asyncio
) &
BACK_PID=$!

cleanup() { kill "$BACK_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "[run] starting frontend on :5173"
cd frontend
npm run dev
