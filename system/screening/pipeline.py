"""End-to-end adjudication. Every hit gets an answer; any error becomes REVIEW (never a clear)."""

from __future__ import annotations

from .compiler import Verdict, combine_persons, compile_person
from .contracts import Decision, HitDecision, ReasonCode, RuleResult, Tier
from .explain import evidence_ids, reason_text
from .facts import gather_hit_facts
from .loaders import Dataset
from .resolve import resolve_customer
from .rules import RULES, Context
from .triage import assign_tier, flags_for


def adjudicate(ds: Dataset, hit_id: str) -> HitDecision:
    hit = ds.hits[hit_id]
    alert = ds.alerts.get(hit.alert_id)
    if alert is None:
        return fallback(hit_id, hit.alert_id, "ALERT_NOT_FOUND")
    resolution = resolve_customer(ds, alert)
    facts = gather_hit_facts(ds, hit)
    verdicts: list[Verdict] = []
    trace: list[RuleResult] = []
    for person in resolution.persons or (None,):
        ctx = Context(ds=ds, alert=alert, hit=hit, facts=facts, person=person)
        results = [r for rule in RULES for r in rule(ctx)]
        trace.extend(results)
        verdicts.append(
            compile_person(
                results,
                is_company=ctx.customer_is_company,
                record_id=person.key if person else None,
            )
        )
    verdict = combine_persons(verdicts)
    flags = flags_for(verdict, resolution_method=resolution.method)
    return HitDecision(
        hit_id=hit.hit_id,
        alert_id=hit.alert_id,
        decision=verdict.decision,
        reason_code=verdict.reason_code,
        customer_record_id=verdict.customer_record_id,
        reason=reason_text(verdict),
        evidence=evidence_ids(verdict),
        tier=assign_tier(verdict, flags),
        flags=flags,
        trace=tuple(trace),
    )


def fallback(hit_id: str, alert_id: str, flag: str) -> HitDecision:
    return HitDecision(
        hit_id=hit_id,
        alert_id=alert_id,
        decision=Decision.REVIEW,
        reason_code=ReasonCode.INSUFFICIENT_DATA,
        customer_record_id=None,
        reason="The engine could not evaluate this hit, so an analyst reviews it.",
        evidence=(),
        tier=Tier.HUMAN_REVIEW,
        flags=(flag,),
    )


def run(ds: Dataset) -> list[HitDecision]:
    out: list[HitDecision] = []
    for hit_id in sorted(ds.hits):
        try:
            out.append(adjudicate(ds, hit_id))
        except Exception as exc:  # noqa: BLE001 - safety net: errors never clear a hit
            out.append(fallback(hit_id, ds.hits[hit_id].alert_id, f"ENGINE_ERROR:{type(exc).__name__}"))
    return out
