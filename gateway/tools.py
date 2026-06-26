"""Pull-Request review tools exposed by the AgentCore Gateway.

These are pure, side-effect-free functions so they can be unit-tested in
isolation *and* wrapped as MCP tools by ``gateway/server.py``. Keeping the
business logic here (rather than inside the MCP decorators) means the same
implementation is reachable from tests, from the runtime, and from the MCP
transport without duplication.

Two tools are implemented:

* ``analyze_diff``        -- lightweight static analysis of a unified diff.
* ``check_test_coverage`` -- flags source files changed without test changes.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Heuristic rule tables.  Intentionally simple and explainable -- every finding
# can be traced back to exactly one rule below.
# --------------------------------------------------------------------------- #

# (regex, severity, rule_id, human message)
_SECURITY_RULES: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"""(?i)(aws_secret_access_key|aws_access_key_id)\s*[=:]"""),
     "critical", "SEC001", "Possible hardcoded AWS credential."),
    (re.compile(r"""(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]{6,}['\"]"""),
     "critical", "SEC002", "Possible hardcoded secret/credential literal."),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
     "critical", "SEC003", "Private key material committed to source."),
    (re.compile(r"(?i)\beval\s*\("),
     "high", "SEC004", "Use of eval() can enable code injection."),
    (re.compile(r"(?i)f['\"].*\b(select|insert|update|delete)\b.*\{"),
     "high", "SEC005", "f-string SQL query may be injectable; use parameters."),
    (re.compile(r"(?i)verify\s*=\s*False"),
     "medium", "SEC006", "TLS verification disabled (verify=False)."),
]

# (regex, severity, rule_id, message)
_QUALITY_RULES: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"(?i)\b(TODO|FIXME|XXX|HACK)\b"),
     "low", "QUA001", "Unresolved TODO/FIXME left in changed code."),
    (re.compile(r"print\s*\("),
     "low", "QUA002", "Debug print() statement in changed code."),
    (re.compile(r"(?i)except\s*:\s*$|except\s+Exception\s*:\s*\n\s*pass"),
     "medium", "QUA003", "Bare/broad except that swallows errors."),
]

_TEST_PATH_HINT = re.compile(r"(^|/)(tests?|__tests__|spec)/|(_test|test_|\.test|\.spec)\.")
_SOURCE_EXT = re.compile(r"\.(py|js|ts|tsx|jsx|go|java|rb|rs|c|cc|cpp|cs)$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _added_lines(diff: str) -> list[tuple[str, str]]:
    """Return (current_file, added_line) tuples for every '+' line in *diff*."""
    out: list[tuple[str, str]] = []
    current = "<unknown>"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            # e.g. "+++ b/path/to/file.py"
            current = line[4:].lstrip()
            if current.startswith("b/"):
                current = current[2:]
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((current, line[1:]))
    return out


def _largest_added_hunk(diff: str) -> int:
    """Largest number of consecutive added lines (a crude complexity proxy)."""
    longest = run = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


# --------------------------------------------------------------------------- #
# Tool 1: analyze_diff
# --------------------------------------------------------------------------- #

def analyze_diff(diff: str, max_hunk_lines: int = 80) -> dict[str, Any]:
    """Run static heuristics over a unified-diff string.

    Returns a structured, explainable report. Every finding carries a
    ``rule_id`` and ``evidence`` snippet so a reviewer (human or agent) can
    audit *why* it was raised.

    Args:
        diff: Unified diff text (``git diff`` output).
        max_hunk_lines: Threshold above which a single added hunk is flagged
            as a large/complex change.

    Returns:
        dict with ``findings`` (list), ``stats`` (dict) and ``summary`` (str).
    """
    if not isinstance(diff, str) or not diff.strip():
        return {
            "findings": [],
            "stats": {"added_lines": 0, "files_touched": 0, "largest_hunk": 0},
            "summary": "Empty diff; nothing to analyze.",
        }

    added = _added_lines(diff)
    files_touched = sorted({f for f, _ in added})
    findings: list[dict[str, Any]] = []

    for rules in (_SECURITY_RULES, _QUALITY_RULES):
        category = "security" if rules is _SECURITY_RULES else "quality"
        for path, line in added:
            for pattern, severity, rule_id, message in rules:
                if pattern.search(line):
                    findings.append({
                        "rule_id": rule_id,
                        "category": category,
                        "severity": severity,
                        "message": message,
                        "file": path,
                        "evidence": line.strip()[:200],
                    })

    largest = _largest_added_hunk(diff)
    if largest > max_hunk_lines:
        findings.append({
            "rule_id": "QUA010",
            "category": "quality",
            "severity": "medium",
            "message": (f"Large single hunk of {largest} added lines "
                        f"(> {max_hunk_lines}); consider splitting the PR."),
            "file": files_touched[0] if files_touched else "<unknown>",
            "evidence": f"{largest} consecutive added lines",
        })

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: sev_rank.get(f["severity"], 9))

    return {
        "findings": findings,
        "stats": {
            "added_lines": len(added),
            "files_touched": len(files_touched),
            "largest_hunk": largest,
        },
        "summary": (f"{len(findings)} finding(s) across {len(files_touched)} "
                    f"file(s); {len(added)} added line(s)."),
    }


# --------------------------------------------------------------------------- #
# Tool 2: check_test_coverage
# --------------------------------------------------------------------------- #

def check_test_coverage(files: list[str]) -> dict[str, Any]:
    """Flag source files that changed without an accompanying test change.

    Pure path-based heuristic -- it does not execute tests. It answers the
    common review question "did this PR touch code but forget the tests?".

    Args:
        files: List of file paths changed in the PR.

    Returns:
        dict with ``source_files``, ``test_files``, ``gaps`` and ``summary``.
    """
    if not isinstance(files, list):
        return {"source_files": [], "test_files": [], "gaps": [],
                "summary": "No file list provided."}

    test_files = [f for f in files if _TEST_PATH_HINT.search(f)]
    source_files = [f for f in files
                    if _SOURCE_EXT.search(f) and not _TEST_PATH_HINT.search(f)]

    has_any_test_change = bool(test_files)
    gaps: list[dict[str, str]] = []
    for src in source_files:
        # crude stem match: does any changed test reference this file's stem?
        stem = re.sub(_SOURCE_EXT, "", src.rsplit("/", 1)[-1])
        covered = any(stem in t for t in test_files)
        if not covered:
            gaps.append({
                "file": src,
                "reason": ("no changed test file references this module"
                           if has_any_test_change
                           else "PR changes source but adds/edits no tests"),
            })

    return {
        "source_files": source_files,
        "test_files": test_files,
        "gaps": gaps,
        "summary": (f"{len(source_files)} source file(s) changed, "
                    f"{len(test_files)} test file(s) changed, "
                    f"{len(gaps)} coverage gap(s)."),
    }


# Registry consumed by the MCP server and the in-process fallback client.
TOOL_REGISTRY = {
    "analyze_diff": analyze_diff,
    "check_test_coverage": check_test_coverage,
}
