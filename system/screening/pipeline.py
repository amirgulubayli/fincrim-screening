"""End-to-end adjudication. Every hit gets an answer; any error becomes REVIEW (never a clear).

Two independent deterministic engines (dual control):

  core   screening.core   - the reviewed AML Bot engine (PR #28): authoritative decision,
                            reason code, customer, reason text and evidence.
  check  modular rules    - resolve -> facts -> rules/* -> compiler: an independent second
                            opinion that also yields the per-facet trace for the UI.

merge (compiler.merge_with_core):
  * the core decides;
  * core clears but the check CONFIRMS (name + DOB agree)  -> REVIEW (never clear on a conflict);
  * any other disagreement                                  -> flag ENGINES_DISAGREE (4-eyes tier).

SCREEN_ENGINE=check|core|combined (default combined) selects the engine, e.g. for rule work.
"""

from __future__ import annotations

import os
import re

from .compiler import Verdict, combine_persons, compile_person, merge_with_core
from .contracts import Decision, Facet, HitDecision, ReasonCode, RuleResult, Signal, Tier
from .explain import evidence_ids, reason_text
from .facts import gather_hit_facts
from .loaders import Dataset
from .normalize.dates import parse_date
from .resolve import resolve_customer
from .rules import RULES, Context
from .triage import assign_tier, flags_for

ENGINES = ("combined", "core", "check")


def engine_mode() -> str:
    mode = os.environ.get("SCREEN_ENGINE", "combined").strip().lower()
    return mode if mode in ENGINES else "combined"


def check_hit(ds: Dataset, hit_id: str) -> HitDecision:
    """The modular, facet-by-facet engine (independent second check)."""
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
    verdict = _guard_location_clear(verdict, hit, alert, resolution.persons, trace)
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


_DOB_KEY = re.compile(r"dob|date.?of.?birth|birth.?date", re.I)


def _date_unusable(hit, alert, persons) -> str | None:
    """Why a date that could tie the hit to the customer is present but unusable, else None.

    Covers unreadable formats (2-digit years, free text), DOBs under a field name the
    loader does not map (left in `extra`), and a screening date too imprecise to turn
    an age into a birth year.
    """
    if any(parse_date(v) is None for v in hit.dobs):
        return "hit DOB unreadable"
    records = [r for p in persons or () for r in p.records]
    for rec in (hit, alert, *records):
        if any(_DOB_KEY.search(k) and v not in (None, "", [], ()) for k, v in rec.extra.items()):
            return "DOB under an unmapped field name"
    raws = [alert.dob, *(r.dob for r in records), *(k.verified_dob for p in persons or () for k in p.kyc)]
    if any(r and parse_date(r) is None for r in raws):
        return "customer DOB unreadable"
    when = parse_date(alert.screened_at) if alert.screened_at else None
    if alert.screened_at and (when is None or not when.day):
        return "screening date imprecise"
    return None


def _guard_location_clear(verdict, hit, alert, persons, trace) -> Verdict:
    """SOP P6 fail-safe: a country difference clears only if the DOBs could be compared.

    The location rule's birth-year safety net needs readable dates; when the names agree
    and a date is present but unusable, the clear becomes REVIEW (never clear on doubt).
    """
    if verdict.decision is not Decision.DIFFERENT_ENTITY or verdict.reason_code is not ReasonCode.LOCATION_MISMATCH:
        return verdict
    if not any(r.facet is Facet.NAME and r.signal is Signal.AGREEMENT for r in trace):
        return verdict
    why = _date_unusable(hit, alert, persons)
    if why is None:
        return verdict
    return Verdict(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, verdict.used, verdict.customer_record_id,
                   (*verdict.notes, f"The names agree and the countries differ, but a date could not be "
                    f"compared ({why}); a country difference alone does not clear this hit."))


def adjudicate(ds: Dataset, hit_id: str, mode: str | None = None) -> HitDecision:
    mode = mode or engine_mode()
    if mode == "check":
        return check_hit(ds, hit_id)
    from .core import core_results

    core = core_results(ds).get(hit_id)
    if mode == "core":
        return merge_with_core(core, None, hit_id, ds.hits[hit_id].alert_id)
    try:
        check = check_hit(ds, hit_id)
    except Exception as exc:  # noqa: BLE001 - the check failing must not block the core
        check = fallback(hit_id, ds.hits[hit_id].alert_id, f"CHECK_ERROR:{type(exc).__name__}")
    return merge_with_core(core, check, hit_id, ds.hits[hit_id].alert_id)


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


def run(ds: Dataset, mode: str | None = None) -> list[HitDecision]:
    mode = mode or engine_mode()
    out: list[HitDecision] = []
    for hit_id in sorted(ds.hits):
        try:
            out.append(adjudicate(ds, hit_id, mode))
        except Exception as exc:  # noqa: BLE001 - safety net: errors never clear a hit
            out.append(fallback(hit_id, ds.hits[hit_id].alert_id, f"ENGINE_ERROR:{type(exc).__name__}"))
    return out
