"""Minimal local stand-in for ``bedrock_agentcore.runtime.BedrockAgentCoreApp``.

Implements just enough of the AgentCore Runtime surface -- an ``@entrypoint``
decorator and a ``run()`` that serves the handler over HTTP -- so the service
is runnable offline without AWS. When the real SDK is installed, ``runtime.app``
uses it instead and this shim is never imported.

The HTTP contract mirrors AgentCore Runtime: ``POST /invocations`` with a JSON
body returns the handler's JSON result; ``GET /ping`` is a health check.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable


class LocalAgentCoreApp:
    def __init__(self) -> None:
        self._handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def entrypoint(self, fn: Callable[[dict[str, Any]], dict[str, Any]]):
        self._handler = fn
        return fn

    def run(self, host: str | None = None, port: int | None = None) -> None:
        # Container deployments set AGENTCORE_HOST=0.0.0.0; locally we default
        # to loopback to avoid Windows reserved-port permission errors.
        host = host or os.environ.get("AGENTCORE_HOST", "127.0.0.1")
        port = port or int(os.environ.get("AGENTCORE_PORT", "8080"))
        if self._handler is None:
            raise RuntimeError("no @entrypoint registered")
        handler = self._handler

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # quiet
                pass

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/ping":
                    self._json(200, {"status": "healthy"})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path not in ("/invocations", "/invoke"):
                    self._json(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                    result = handler(payload)
                    self._json(200, result)
                except Exception as exc:  # noqa: BLE001
                    self._json(500, {"error": str(exc)})

            def _json(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        HTTPServer((host, port), _H).serve_forever()
