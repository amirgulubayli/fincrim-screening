"""P6 Name: verified legal name agrees with none of the hit names/aliases -> NAME_MISMATCH_VERIFIED (only with a verified name from an approved check). P8 agreement emits AGREEMENT. Fewer than two real parts on an individual -> neither confirm nor clear. Flag weak=True on edit-distance-only matches.

TODO seed issue. Until implemented this check abstains (returns []), which is
safe: with no contradiction and no agreement the compiler answers REVIEW.
"""

from __future__ import annotations

from ..contracts import RuleResult
from .base import Context

RULE_ID = "P6.name"


def check(ctx: Context) -> list[RuleResult]:
    return []
