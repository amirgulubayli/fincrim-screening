"""Deterministic reason text (2-4 sentences naming the decisive facts).

Templates only, no LLM, so the text is identical on every run. Any LLM rewrite
for the dashboard lives in app/, never in the scored output.
"""

from __future__ import annotations

from .compiler import Verdict
from .contracts import CUSTOMER_AMBIGUOUS, Decision, ReasonCode, RuleResult


def _sentences(results: tuple[RuleResult, ...]) -> list[str]:
    return list(dict.fromkeys(r.summary.strip() for r in results if r.summary.strip()))


def reason_text(verdict: Verdict) -> str:
    parts = _sentences(verdict.used)
    if verdict.decision is Decision.DIFFERENT_ENTITY:
        parts.append("This contradiction shows the hit is a different entity, so it is cleared.")
    elif verdict.decision is Decision.SAME_ENTITY:
        parts.append("Nothing contradicts the match, so the hit goes to the compliance team.")
    elif verdict.reason_code is ReasonCode.AMBIGUOUS_CUSTOMER_RECORD:
        parts = [*verdict.notes, "An analyst must decide which customer was screened."]
    else:
        parts = [*verdict.notes, *parts]
        parts.append(
            "No fact contradicts the hit and there is not enough agreement to confirm it, "
            "so an analyst reviews it."
        )
    return " ".join(parts[:4])


def evidence_ids(verdict: Verdict) -> tuple[str, ...]:
    ids: list[str] = []
    if verdict.customer_record_id and verdict.customer_record_id != CUSTOMER_AMBIGUOUS:
        ids.append(verdict.customer_record_id)
    for r in verdict.used:
        ids.extend(r.evidence)
    return tuple(sorted(dict.fromkeys(i for i in ids if i)))
