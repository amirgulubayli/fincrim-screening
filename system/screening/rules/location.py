"""P6 Location: EVERY country tied to the customer differs from EVERY country tied to the hit -> LOCATION_MISMATCH. Agreement when any country is shared. The compiler turns a location-only contradiction into REVIEW when name and DOB agree.

TODO seed issue. Until implemented this check abstains (returns []), which is
safe: with no contradiction and no agreement the compiler answers REVIEW.
"""

from __future__ import annotations

from ..contracts import RuleResult
from .base import Context

RULE_ID = "P6.location"


def check(ctx: Context) -> list[RuleResult]:
    return []
