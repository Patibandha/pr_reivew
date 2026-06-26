"""AgentCore Runtime service -- entrypoint for the PR-review agent.

On AWS, AgentCore Runtime hosts an agent behind an ``invoke`` entrypoint and
streams a JSON request in / JSON response out. We mirror that contract here:

* ``@entrypoint``-decorated ``invoke(payload)`` is the single handler. If the
  real ``bedrock_agentcore`` SDK is installed it is used verbatim; otherwise a
  tiny local shim provides the same ``app.entrypoint`` / ``app.run`` surface so
  the service is runnable offline.
* ``handler(payload)`` is the plain function used by tests and the local CLI.

Run as a local HTTP service:
    python -m runtime.app            # serves invoke on :8080 (shim or SDK)

Invoke without a server (one-shot):
    python -m client.invoke examples/input.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

from runtime.workflow import run_review

# --------------------------------------------------------------------------- #
# Use the real AgentCore SDK when present; otherwise a minimal local shim that
# implements the same decorator + run() surface.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised only when the SDK is installed
    from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore

    app = BedrockAgentCoreApp()
    _USING_SDK = True
except Exception:  # noqa: BLE001
    from runtime._shim import LocalAgentCoreApp

    app = LocalAgentCoreApp()
    _USING_SDK = False


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    """AgentCore entrypoint: review a pull request, return a structured review."""
    return run_review(payload)


def handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Direct, transport-free handler (used by tests and the one-shot CLI)."""
    return run_review(payload)


def _main(argv: list[str]) -> int:
    # `python -m runtime.app <input.json>` -> one-shot to stdout.
    # `python -m runtime.app`              -> start the runtime server.
    if len(argv) > 1:
        with open(argv[1], "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        print(json.dumps(handler(payload), indent=2))
        return 0
    print(f"Starting AgentCore Runtime (SDK={_USING_SDK}) on :8080 ...",
          file=sys.stderr)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
