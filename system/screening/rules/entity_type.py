"""P6 Type: one is a person and the other a company -> DIFFERENT_ENTITY.

Reference implementation: copy this shape for the other rules.
"""

from __future__ import annotations

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from .base import Context, is_company, is_person

RULE_ID = "P6.type"


def check(ctx: Context) -> list[RuleResult]:
    cust_type = (ctx.person.records[0].entity_type if ctx.person else None) or ctx.alert.entity_type
    hit_type = ctx.hit.entity_type
    known = (is_person(cust_type) or is_company(cust_type)) and (
        is_person(hit_type) or is_company(hit_type)
    )
    if not known:
        return []
    evidence = ctx.person.record_ids if ctx.person else ()
    if is_company(cust_type) != is_company(hit_type):
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.TYPE,
                signal=Signal.CONTRADICTION,
                reason=ReasonCode.ENTITY_TYPE_MISMATCH,
                summary=f"The customer is a {'company' if is_company(cust_type) else 'person'} "
                f"but the hit is a {'company' if is_company(hit_type) else 'person'}.",
                evidence=evidence,
            )
        ]
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.TYPE,
            signal=Signal.NEUTRAL,
            summary="Customer and hit are the same kind of entity.",
            evidence=(),
        )
    ]
