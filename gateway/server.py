"""AgentCore Gateway -- MCP server exposing the PR-review stub tools.

In a real AWS deployment, AgentCore Gateway fronts these tools and handles
auth/transport. Locally we expose the exact same tool contract over the
standard MCP stdio transport using the official ``mcp`` SDK (FastMCP), so the
runtime talks to them through a genuine MCP client.

Run standalone (for inspection / `mcp` CLI):
    python -m gateway.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from gateway import tools

mcp = FastMCP("agentcore-pr-gateway")


@mcp.tool()
def analyze_diff(diff: str, max_hunk_lines: int = 80) -> dict[str, Any]:
    """Static-analyze a unified diff for security and quality findings.

    Args:
        diff: Unified diff text (git diff output).
        max_hunk_lines: Size above which a single added hunk is flagged.
    """
    return tools.analyze_diff(diff, max_hunk_lines=max_hunk_lines)


@mcp.tool()
def check_test_coverage(files: list[str]) -> dict[str, Any]:
    """Flag changed source files that lack an accompanying test change.

    Args:
        files: List of file paths changed in the PR.
    """
    return tools.check_test_coverage(files)


if __name__ == "__main__":
    # Default stdio transport -- what the runtime's MCP client spawns.
    mcp.run()
