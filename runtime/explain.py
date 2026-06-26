"""Explainability helpers for the PR-review agent.

The agent must be able to justify *every* output:

* a step-by-step **reasoning trace** of what it did and why,
* the **tool calls** it made (name, args, transport, raw result),
* a **confidence** score with the factors that produced it,
* the **decision rationale** linking findings to the verdict.

This module just structures that evidence; the workflow feeds it in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReasoningTrace:
    steps: list[dict[str, str]] = field(default_factory=list)

    def add(self, action: str, detail: str) -> None:
        self.steps.append({"step": str(len(self.steps) + 1),
                           "action": action, "detail": detail})

    def to_list(self) -> list[dict[str, str]]:
        return self.steps


# Severity -> weight used for the risk score.
_SEV_WEIGHT = {"critical": 1.0, "high": 0.6, "medium": 0.3, "low": 0.1}


def score_confidence(num_findings: int, used_real_tools: bool,
                     guardrail_blocked: bool, coverage_gaps: int) -> dict[str, Any]:
    """Heuristic, transparent confidence score in [0, 1].

    Higher when the agent had real tool signal and a clean guardrail pass;
    lower when it had to fall back or saw conflicting/sparse evidence.
    """
    factors: list[dict[str, Any]] = []
    score = 0.5
    factors.append({"factor": "base", "delta": 0.5,
                    "reason": "neutral prior"})

    if used_real_tools:
        score += 0.25
        factors.append({"factor": "real_tool_signal", "delta": 0.25,
                        "reason": "tools executed over MCP and returned data"})
    else:
        score -= 0.1
        factors.append({"factor": "fallback_transport", "delta": -0.1,
                        "reason": "MCP unavailable; used in-process fallback"})

    if num_findings > 0:
        score += 0.15
        factors.append({"factor": "actionable_findings", "delta": 0.15,
                        "reason": f"{num_findings} concrete finding(s) to cite"})

    if coverage_gaps > 0:
        score += 0.05
        factors.append({"factor": "coverage_signal", "delta": 0.05,
                        "reason": f"{coverage_gaps} test-coverage gap(s) detected"})

    if guardrail_blocked:
        score = 0.2
        factors = [{"factor": "guardrail_block", "delta": 0.0,
                    "reason": "request blocked; low confidence by design"}]

    score = max(0.0, min(1.0, round(score, 2)))
    return {"score": score, "factors": factors}


def risk_level(findings: list[dict[str, Any]], coverage_gaps: int) -> dict[str, Any]:
    """Aggregate findings into an overall risk band + numeric score."""
    raw = sum(_SEV_WEIGHT.get(f.get("severity", "low"), 0.1) for f in findings)
    raw += 0.1 * coverage_gaps
    if any(f.get("severity") == "critical" for f in findings):
        band = "high"
    elif raw >= 1.0:
        band = "high"
    elif raw >= 0.4:
        band = "medium"
    else:
        band = "low"
    return {"band": band, "raw_score": round(raw, 2)}


def decide(findings: list[dict[str, Any]], coverage_gaps: int) -> dict[str, Any]:
    """Map evidence to an explainable verdict."""
    criticals = [f for f in findings if f.get("severity") == "critical"]
    highs = [f for f in findings if f.get("severity") == "high"]

    if criticals:
        verdict = "request_changes"
        rationale = (f"{len(criticals)} critical finding(s) "
                     "(e.g. possible secret leak) must be resolved before merge.")
    elif highs or coverage_gaps >= 2:
        verdict = "request_changes"
        rationale = (f"{len(highs)} high-severity finding(s) and "
                     f"{coverage_gaps} coverage gap(s) warrant changes.")
    elif findings or coverage_gaps:
        verdict = "comment"
        rationale = ("Only low/medium issues; reviewer should weigh in but "
                     "merge is not blocked.")
    else:
        verdict = "approve"
        rationale = "No findings from static analysis or coverage checks."
    return {"verdict": verdict, "rationale": rationale}
