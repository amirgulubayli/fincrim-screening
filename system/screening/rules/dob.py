"""P6 DOB: every hit birth date contradicted (year >1 off, or same year with different month/day) -> DOB_MISMATCH. P7: agreement when the customer's year is one of the hit's possible years and every shared part is equal. Use the latest APPROVED KYC dob first (verified beats declared). Flag weak=True when the hit DOB came only from search results.

TODO seed issue. Until implemented this check abstains (returns []), which is
safe: with no contradiction and no agreement the compiler answers REVIEW.
"""

from __future__ import annotations

from ..contracts import RuleResult
from .base import Context

RULE_ID = "P6.dob"


def check(ctx: Context) -> list[RuleResult]:
    return []
