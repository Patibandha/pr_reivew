"""Convenience runner: review examples/input.json and write examples/output.json.

Equivalent to:
    python -m client.invoke examples/input.json -o examples/output.json

Usage:
    python run.py                 # uses examples/input.json -> examples/output.json
    python run.py path/to/in.json # prints result to stdout
"""

from __future__ import annotations

import os
import sys

from client.invoke import main

if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(main([sys.argv[1]]))
    here = os.path.dirname(os.path.abspath(__file__))
    raise SystemExit(main([
        os.path.join(here, "examples", "input.json"),
        "-o", os.path.join(here, "examples", "output.json"),
    ]))
