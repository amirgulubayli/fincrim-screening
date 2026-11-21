"""P6 Time: the hit's profile gives a date of death before the customer was screened -> DECEASED.

TODO seed issue. Until implemented this check abstains (returns []), which is
safe: with no contradiction and no agreement the compiler answers REVIEW.
"""

from __future__ import annotations

from ..contracts import RuleResult
from .base import Context

RULE_ID = "P6.deceased"


def check(ctx: Context) -> list[RuleResult]:
    return []
