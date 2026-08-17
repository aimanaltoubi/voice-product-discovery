"""Offline end-to-end smoke test — no API keys, no network.

Runs the whole pipeline with:
    LLM_PROVIDER=mock          (deterministic heuristics)
    EMBEDDINGS_PROVIDER=hash   (test-only hashing embedder)
so it works in CI / sandboxes. web.search may return zero results offline;
the pipeline must degrade gracefully rather than crash.

Usage (from repo root):  python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

# Must be set before app.config is imported anywhere.
_TEST_ROOT = tempfile.TemporaryDirectory(prefix="pickly-smoke-")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("EMBEDDINGS_PROVIDER", "hash")
os.environ.setdefault("STORAGE_DIR", str(Path(_TEST_ROOT.name) / "storage"))
os.environ.setdefault("PROCESSED_DIR", str(Path(_TEST_ROOT.name) / "processed"))
sys.path.insert(0, str(BACKEND))


def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


async def main() -> int:
    banner("1/3  Ingesting the synthetic sample catalog (hash embeddings)")
    from rag.ingest import main as ingest_main

    ingest_main(["--sample"])

    banner("2/3  Starting MCP server (stdio) + tool discovery")
    from mcp_server.client import MCPToolClient

    mcp = MCPToolClient()
    await mcp.start()
    names = [t["name"] for t in mcp.tool_catalog]
    print("Discovered tools:", names)
    assert set(names) == {"web.search", "rag.search"}, f"unexpected tools: {names}"
    for t in mcp.tool_catalog:
        props = list((t["input_schema"] or {}).get("properties", {}).keys())
        print(f"  - {t['name']}: params {props}")

    banner("3/3  Running discovery scenarios through the LangGraph pipeline")
    from graph.build import run_discovery

    scenarios = [
        ("eco/private", "Find me an eco-friendly stainless steel cleaner under fifteen dollars"),
        ("needs_live", "What's the current price of a glass cleaner right now?"),
        ("safety", "Can I mix bleach and ammonia to make a stronger cleaner?"),
    ]
    failures = 0
    for label, transcript in scenarios:
        print(f"\n--- scenario: {label!r} ---\n    “{transcript}”")
        payload = await run_discovery(transcript, mcp)
        steps = [s["name"] for s in payload["steps"]]
        print("    steps:", " -> ".join(steps))
        print("    spoken:", payload["spoken_answer"][:160])
        try:
            assert payload["transcript"] == transcript
            assert payload["spoken_answer"], "empty spoken_answer"
            if label == "safety":
                assert payload["blocked"] is True
                assert "safety" in steps
            else:
                assert payload["blocked"] is False
                assert "router" in steps and "planner" in steps and "rag.search" in steps
                if payload["comparison_table"]:
                    assert payload["citations"], "grounded products need citations"
                    assert payload["top_pick"] and payload["top_pick"]["doc_id"]
                else:
                    # Live search is allowed to return zero rows offline. The
                    # graph must surface that state honestly, not manufacture
                    # a product/card/citation to satisfy the smoke test.
                    assert label == "needs_live", "private scenario lost all products"
                    assert payload["live_unverified"], "missing live no-match state"
                    assert not payload["citations"], "empty result must not cite a product"
                    assert payload["top_pick"] is None, "empty result must not name a top pick"
            if label == "needs_live":
                assert "web.search" in steps, "planner should have added web.search"
        except AssertionError as e:
            failures += 1
            print(f"    FAIL: {e}")
        else:
            print("    OK")

    await mcp.stop()
    banner(f"Smoke test {'PASSED' if failures == 0 else f'FAILED ({failures})'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
