"""SOP checks. One module per facet so people can work in parallel without conflicts.

Every check has the signature  check(ctx: Context) -> list[RuleResult]
and must be PURE and DETERMINISTIC (no randomness, no network, no clock).
Return [] when the check has nothing to say. Never raise for messy data:
unknown stays unknown (a missing fact is never a contradiction).
"""

from __future__ import annotations

from . import deceased, dob, entity_type, location, name, temporal
from .base import Context

RULES = (
    entity_type.check,  # P6 type
    deceased.check,  # P6 time (date of death)
    temporal.check,  # P6 time (too young / not incorporated)
    dob.check,  # P6 DOB + P7 DOB agreement
    name.check,  # P6 name + P8
    location.check,  # P6 location + country agreement
)

__all__ = ["RULES", "Context"]
