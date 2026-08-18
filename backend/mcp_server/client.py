"""MCP client wrapper.

The FastAPI app (and the LangGraph nodes it runs) talk to the MCP tool
server through this client. The server is launched as a subprocess and
spoken to over stdio using the official `mcp` Python SDK — i.e. the
agent really does discover and invoke `web.search` / `rag.search` via
the Model Context Protocol rather than importing them as functions.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[1]


class MCPToolClient:
    """Manages one stdio MCP server subprocess + session.

    Usage:
        client = MCPToolClient()
        await client.start()
        tools = client.tool_catalog          # discovered via list_tools()
        out = await client.call("rag.search", {"query": "..."} )
        await client.stop()
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self.tool_catalog: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._session is not None:
            return
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(BACKEND_DIR),
            env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
        )
        # Jupyter/Colab's sys.stderr has no real OS fileno(), which the mcp SDK
        # needs when spawning the server subprocess. Use a real file instead.
        _errlog = open(BACKEND_DIR / "logs" / "mcp_stdio_stderr.log", "w")
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=_errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

        # Tool discovery — the client learns the tool names + JSON schemas
        # from the server; nothing is hard-coded.
        listed = await self._session.list_tools()
        self.tool_catalog = [
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "input_schema": t.inputSchema,
            }
            for t in listed.tools
        ]

    async def stop(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack = None
        self._session = None
        self.tool_catalog = []

    # -- calls -------------------------------------------------------------

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool and return its JSON payload as a dict."""
        if self._session is None:
            raise RuntimeError("MCP client not started")
        async with self._lock:  # one stdio pipe -> serialize calls
            result = await self._session.call_tool(name, arguments)

        # Prefer structured content when the SDK provides it.
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            # FastMCP wraps plain-dict returns as {"result": {...}} sometimes;
            # unwrap if that's the only key.
            if set(structured.keys()) == {"result"} and isinstance(structured["result"], dict):
                return structured["result"]
            return structured

        for block in result.content or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"results": parsed}
                except json.JSONDecodeError:
                    return {"raw": text}
        return {"results": []}
