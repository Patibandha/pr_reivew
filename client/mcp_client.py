"""MCP client used by the runtime to call AgentCore Gateway tools.

Primary path: spawn ``gateway/server.py`` over the standard MCP **stdio**
transport using the official ``mcp`` SDK and call tools through a real
``ClientSession``. This is the same wire protocol AgentCore Gateway speaks.

Fallback path: if the ``mcp`` package is unavailable, call the tool functions
in-process via ``TOOL_REGISTRY``. The fallback keeps the workflow runnable in
constrained environments; the transport actually used is reported back for
explainability so the distinction is never hidden.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from gateway.tools import TOOL_REGISTRY


@dataclass
class ToolCall:
    """A single recorded tool invocation (for explainability)."""
    name: str
    arguments: dict[str, Any]
    result: Any
    transport: str
    ok: bool = True
    error: str | None = None


@dataclass
class GatewayClient:
    """Thin wrapper that records every call it makes."""
    prefer_mcp: bool = True
    calls: list[ToolCall] = field(default_factory=list)
    transport: str = "uninitialized"

    def call(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        if self.prefer_mcp:
            try:
                result = asyncio.run(_call_over_mcp(name, arguments))
                call = ToolCall(name, arguments, result, "mcp-stdio")
                self.transport = "mcp-stdio"
                self.calls.append(call)
                return call
            except Exception as exc:  # noqa: BLE001 - fall back, but record why
                fallback_reason = f"{type(exc).__name__}: {exc}"
        else:
            fallback_reason = "prefer_mcp=False"

        # In-process fallback.
        try:
            fn = TOOL_REGISTRY[name]
            result = fn(**arguments)
            call = ToolCall(name, arguments, result,
                            f"in-process (fallback: {fallback_reason})")
        except Exception as exc:  # noqa: BLE001
            call = ToolCall(name, arguments, None,
                            "in-process", ok=False, error=str(exc))
        self.transport = call.transport
        self.calls.append(call)
        return call


async def _call_over_mcp(name: str, arguments: dict[str, Any]) -> Any:
    """Spawn the gateway server over stdio and invoke a single tool."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            return _unwrap(result)


def _unwrap(result: Any) -> Any:
    """Extract structured content from an MCP CallToolResult."""
    # Newer SDKs expose structuredContent directly.
    structured = getattr(result, "structuredContent", None)
    if structured:
        # FastMCP wraps non-dict returns under {"result": ...}; unwrap dicts.
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    # Otherwise parse the first text content block as JSON.
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


async def list_tools() -> list[str]:
    """Return tool names advertised by the gateway (diagnostic helper)."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable,
                                    args=["-m", "gateway.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.list_tools()
            return [t.name for t in resp.tools]
