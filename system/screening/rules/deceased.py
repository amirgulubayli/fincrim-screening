"""P6 Time: the hit's profile gives a date of death before the customer was screened -> DECEASED.

Only a parsed death date counts (facts.deceased_on). `life_status: Deceased` with
no date abstains: a missing fact is never a contradiction. The death date must be
definitely before `screened_at`, comparing only the parts both give, so a
year-only death in the screening year is not before it.
"""

from __future__ import annotations

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.dates import definitely_before, parse_date
from .base import Context

RULE_ID = "P6.deceased"


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # noqa: BLE001 - messy data must never break a run; abstain
        return []


def _check(ctx: Context) -> list[RuleResult]:
    died = ctx.facts.deceased_on
    screened = parse_date(ctx.alert.screened_at)
    if died is None or screened is None:
        return []
    evidence = ctx.person.record_ids if ctx.person else ()
    if definitely_before(died, screened):
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.DECEASED,
                signal=Signal.CONTRADICTION,
                reason=ReasonCode.DECEASED,
                summary=f"The hit's profile gives a date of death ({died}) before the customer "
                f"was screened ({screened}).",
                evidence=evidence,
            )
        ]
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.DECEASED,
            signal=Signal.NEUTRAL,
            summary=f"The hit's date of death ({died}) is not clearly before the screening "
            f"date ({screened}).",
            evidence=(),
        )
    ]
