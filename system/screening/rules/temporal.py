"""P6 Time: customer younger than 16 at the earliest dated report, or company incorporated after it -> TEMPORAL_IMPOSSIBILITY. Dated reports are ONLY lines like 'According to ...; September 10, 2025: ...'.

TODO seed issue. Until implemented this check abstains (returns []), which is
safe: with no contradiction and no agreement the compiler answers REVIEW.
"""

from __future__ import annotations

from ..contracts import RuleResult
from .base import Context

RULE_ID = "P6.temporal"


def check(ctx: Context) -> list[RuleResult]:
    return []
