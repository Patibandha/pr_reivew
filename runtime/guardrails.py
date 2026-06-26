"""Guardrails for the PR-review agent.

Three layers, all explainable (each action is recorded with a reason):

1. **Input validation** -- shape/size limits on the incoming request.
2. **Prompt-injection screening** -- detect attempts in PR text to override
   the agent's instructions.
3. **Sensitive-data redaction** -- detect & mask secrets and PHI/PII before
   anything is echoed into the review output. Per the assignment, no real PHI
   is ever stored or emitted; matches are masked in place.

Nothing here calls an LLM -- guardrails are deterministic so their behaviour is
auditable and reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_DIFF_BYTES = 1_000_000   # 1 MB
MAX_FILES = 500

# --------------------------------------------------------------------------- #
# Detection patterns
# --------------------------------------------------------------------------- #

_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(?:all\s+|any\s+|the\s+|these\s+|previous\s+|prior\s+|"
               r"above\s+|earlier\s+)*(instructions|prompts|messages)"),
    re.compile(r"(?i)disregard (the )?(system|previous|above)"),
    re.compile(r"(?i)you are now (a|an|in) "),
    re.compile(r"(?i)(reveal|print|show) (your|the) (system )?prompt"),
    re.compile(r"(?i)approve this pr (no matter|regardless|without)"),
    re.compile(r"(?i)act as (a |an )?(developer mode|DAN|jailbreak)"),
]

# (regex, label, mask) -- secrets & PHI/PII.
#
# Patterns may use a named group ``(?P<secret>...)`` to mask only the *value*
# while preserving surrounding context (e.g. keep ``API_KEY =`` but mask the
# literal). Patterns without that group are masked whole.
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws_access_key_id", "[REDACTED:AWS_KEY]"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
     "private_key", "[REDACTED:PRIVATE_KEY]"),
    (re.compile(r"(?i)(ghp|github_pat)_[A-Za-z0-9_]{20,}"),
     "github_token", "[REDACTED:GH_TOKEN]"),
    # AWS secret access key assigned to a variable -> mask the value only.
    (re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?(?P<secret>[A-Za-z0-9/+=]{20,})"),
     "aws_secret_access_key", "[REDACTED:AWS_SECRET]"),
    # Generic "name = '<secret>'" assignment for key/secret/token/password.
    (re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[=:]\s*"
                r"['\"](?P<secret>[^'\"]{6,})['\"]"),
     "credential", "[REDACTED:CREDENTIAL]"),
    # Provider-style tokens (sk-..., sk-live_..., sk-ant-...).
    (re.compile(r"(?i)\b(?P<secret>sk[-_][A-Za-z0-9_]{8,})\b"),
     "api_key", "[REDACTED:API_KEY]"),
    # PHI / PII
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn", "[REDACTED:SSN]"),
    (re.compile(r"(?i)\bMRN[:#]?\s*\d{5,}\b"), "medical_record_number",
     "[REDACTED:MRN]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "email", "[REDACTED:EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
     "phone", "[REDACTED:PHONE]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"), "date_of_birth",
     "[REDACTED:DOB]"),
]


@dataclass
class GuardrailAction:
    layer: str            # "input" | "injection" | "redaction"
    rule: str
    detail: str
    severity: str = "info"


@dataclass
class GuardrailReport:
    ok: bool = True
    blocked: bool = False
    block_reason: str | None = None
    actions: list[GuardrailAction] = field(default_factory=list)

    def add(self, action: GuardrailAction) -> None:
        self.actions.append(action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "actions": [a.__dict__ for a in self.actions],
            "counts": self._counts(),
        }

    def _counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.layer] = out.get(a.layer, 0) + 1
        return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def validate_input(payload: dict[str, Any], report: GuardrailReport) -> bool:
    """Validate request shape and size. Mutates *report*; returns False if blocked."""
    if not isinstance(payload, dict):
        report.blocked = True
        report.ok = False
        report.block_reason = "payload must be a JSON object"
        return False

    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        report.blocked = True
        report.ok = False
        report.block_reason = "missing required 'pull_request' object"
        return False

    diff = pr.get("diff", "")
    if isinstance(diff, str) and len(diff.encode("utf-8", "ignore")) > MAX_DIFF_BYTES:
        report.blocked = True
        report.ok = False
        report.block_reason = f"diff exceeds {MAX_DIFF_BYTES} bytes"
        return False

    files = pr.get("files", [])
    if isinstance(files, list) and len(files) > MAX_FILES:
        report.blocked = True
        report.ok = False
        report.block_reason = f"too many files (> {MAX_FILES})"
        return False

    report.add(GuardrailAction("input", "shape_ok",
                               "payload validated (pull_request present, "
                               "within size limits)"))
    return True


def screen_injection(text: str, report: GuardrailReport, *, where: str) -> None:
    """Record (but do not block) prompt-injection attempts in untrusted text.

    PR descriptions are untrusted input. We flag injection attempts so the
    synthesis step can be told to ignore embedded instructions, and so the
    finding is visible to a human reviewer.
    """
    if not isinstance(text, str):
        return
    for pat in _INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            report.add(GuardrailAction(
                "injection", "prompt_injection_suspected",
                f"in {where}: matched '{m.group(0)[:60]}'",
                severity="high"))


def redact(text: str, report: GuardrailReport, *, where: str) -> str:
    """Return *text* with secrets/PHI masked, recording each redaction."""
    if not isinstance(text, str) or not text:
        return text
    redacted = text
    for pattern, label, mask in _SENSITIVE_PATTERNS:
        has_value_group = "secret" in pattern.groupindex

        def _sub(m: re.Match[str], _label: str = label, _mask: str = mask,
                 _grp: bool = has_value_group) -> str:
            report.add(GuardrailAction(
                "redaction", _label, f"masked {_label} in {where}",
                severity="high" if ("key" in _label or "secret" in _label
                or "credential" in _label or _label in
                {"private_key", "ssn", "medical_record_number"}) else "medium"))
            if _grp:
                # Replace only the secret value, keep surrounding context.
                return m.group(0).replace(m.group("secret"), _mask)
            return _mask
        redacted = pattern.sub(_sub, redacted)
    return redacted


def redact_obj(obj: Any, report: GuardrailReport, *, where: str) -> Any:
    """Recursively redact every string inside a JSON-like object."""
    if isinstance(obj, str):
        return redact(obj, report, where=where)
    if isinstance(obj, list):
        return [redact_obj(v, report, where=where) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v, report, where=f"{where}.{k}")
                for k, v in obj.items()}
    return obj
