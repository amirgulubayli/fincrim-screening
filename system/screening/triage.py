"""Human-in-the-loop routing: what is automated, what is borderline, what needs a person.

  REVIEW                                   -> HUMAN_REVIEW  (analyst queue)
  automated decision resting on weak facts -> BORDERLINE    (4-eyes QA)
  DIFFERENT_ENTITY on a solid contradiction -> AUTO_CLEAR
  SAME_ENTITY on solid agreement           -> AUTO_ESCALATE (compliance team)
"""

from __future__ import annotations

from .compiler import Verdict
from .contracts import Decision, Tier


def flags_for(verdict: Verdict, *, resolution_method: str) -> tuple[str, ...]:
    flags = [r.weak_why or f"WEAK_{r.facet.value.upper()}" for r in verdict.used if r.weak]
    if resolution_method in {"unlinked", "none"} and verdict.decision is not Decision.REVIEW:
        flags.append("NO_CUSTOMER_RECORD")
    return tuple(dict.fromkeys(flags))


def assign_tier(verdict: Verdict, flags: tuple[str, ...]) -> Tier:
    if verdict.decision is Decision.REVIEW:
        return Tier.HUMAN_REVIEW
    if flags:
        return Tier.BORDERLINE
    if verdict.decision is Decision.DIFFERENT_ENTITY:
        return Tier.AUTO_CLEAR
    return Tier.AUTO_ESCALATE
