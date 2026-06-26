"""One-shot client: invoke the PR-review agent on a JSON input file.

Two modes:

* Default (in-process): import the runtime handler and run it directly. No
  server needed -- ideal for grading and CI.
* ``--http URL``: POST the payload to a running AgentCore Runtime (or the local
  shim) at ``URL/invocations``.

Usage:
    python -m client.invoke examples/input.json
    python -m client.invoke examples/input.json -o examples/output.json
    python -m client.invoke examples/input.json --http http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _via_http(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    import urllib.request

    endpoint = url.rstrip("/") + "/invocations"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - local/trusted URL
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke the PR-review agent.")
    parser.add_argument("input", help="Path to input JSON file.")
    parser.add_argument("-o", "--output", help="Write result JSON to this path.")
    parser.add_argument("--http", help="Invoke a running runtime at this base URL.")
    args = parser.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if args.http:
        result = _via_http(args.http, payload)
    else:
        from runtime.app import handler
        result = handler(payload)

    text = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
