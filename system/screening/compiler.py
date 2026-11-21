"""The policy compiler: rule results -> one decision per hit (SOP principles 3, 6, 7).

Deterministic precedence, written once, so rule authors only state facts:
  1. any CONTRADICTION clears the hit (DIFFERENT_ENTITY), picked by fixed priority,
     EXCEPT a location-only contradiction when name and DOB agree -> REVIEW
  2. person: name + DOB agree -> SAME_ENTITY (NAME_DOB_LOCATION_MATCH if a country
     agrees too, else NAME_DOB_MATCH); company: name + country -> NAME_LOCATION_MATCH
  3. otherwise REVIEW / INSUFFICIENT_DATA
Several persons fit the alert: decide for each; same decision -> keep it with
customer_record_id "AMBIGUOUS"; different -> REVIEW / AMBIGUOUS_CUSTOMER_RECORD.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    CUSTOMER_AMBIGUOUS,
    REASONS_BY_DECISION,
    Decision,
    Facet,
    ReasonCode,
    RuleResult,
    Signal,
)

CONTRADICTION_PRIORITY = (
    ReasonCode.ENTITY_TYPE_MISMATCH,
    ReasonCode.DECEASED,
    ReasonCode.TEMPORAL_IMPOSSIBILITY,
    ReasonCode.DOB_MISMATCH,
    ReasonCode.LOCATION_MISMATCH,  # same order as the decision core (POLICY.md order)
    ReasonCode.NAME_MISMATCH_VERIFIED,
)


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason_code: ReasonCode
    used: tuple[RuleResult, ...]  # the results that DECIDED it (cited as evidence)
    customer_record_id: str | None
    notes: tuple[str, ...] = ()  # compiler-level explanations


def compile_person(
    results: list[RuleResult], *, is_company: bool, record_id: str | None
) -> Verdict:
    contradictions = [r for r in results if r.signal is Signal.CONTRADICTION]
    agree = {r.facet: r for r in results if r.signal is Signal.AGREEMENT}
    name_dob_agree = Facet.NAME in agree and Facet.DOB in agree

    if contradictions:
        non_location = [r for r in contradictions if r.reason is not ReasonCode.LOCATION_MISMATCH]
        if not non_location and name_dob_agree:
            return Verdict(
                Decision.REVIEW,
                ReasonCode.INSUFFICIENT_DATA,
                (agree[Facet.NAME], agree[Facet.DOB], *contradictions),
                record_id,
                (
                    "Name and date of birth agree but the countries differ; "
                    "a country difference alone does not clear this hit.",
                ),
            )
        best = min(contradictions, key=lambda r: CONTRADICTION_PRIORITY.index(r.reason))
        return Verdict(Decision.DIFFERENT_ENTITY, best.reason, (best,), record_id)

    if is_company:
        if Facet.NAME in agree and Facet.LOCATION in agree:
            return Verdict(
                Decision.SAME_ENTITY,
                ReasonCode.NAME_LOCATION_MATCH,
                (agree[Facet.NAME], agree[Facet.LOCATION]),
                record_id,
            )
    elif name_dob_agree:
        if Facet.LOCATION in agree:
            return Verdict(
                Decision.SAME_ENTITY,
                ReasonCode.NAME_DOB_LOCATION_MATCH,
                (agree[Facet.NAME], agree[Facet.DOB], agree[Facet.LOCATION]),
                record_id,
            )
        return Verdict(
            Decision.SAME_ENTITY,
            ReasonCode.NAME_DOB_MATCH,
            (agree[Facet.NAME], agree[Facet.DOB]),
            record_id,
        )

    return Verdict(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, tuple(agree.values()), record_id)


def combine_persons(verdicts: list[Verdict]) -> Verdict:
    """Apply SOP principle 3 across several candidate persons."""
    if len(verdicts) == 1:
        return verdicts[0]
    if len({v.decision for v in verdicts}) == 1:
        first = verdicts[0]
        used = tuple(r for v in verdicts for r in v.used)
        return Verdict(
            first.decision,
            first.reason_code,
            used,
            CUSTOMER_AMBIGUOUS,
            ("More than one customer fits the alert; all lead to the same decision.",),
        )
    return Verdict(
        Decision.REVIEW,
        ReasonCode.AMBIGUOUS_CUSTOMER_RECORD,
        (),
        CUSTOMER_AMBIGUOUS,
        ("More than one customer fits the alert and they lead to different decisions.",),
    )


# --------------------------------------------------------------------------- dual control

ENGINES_CONFLICT = "ENGINES_CONFLICT"  # core clears, independent check confirms -> REVIEW
ENGINES_DISAGREE = "ENGINES_DISAGREE"  # any other disagreement -> 4-eyes tier
ESCALATED_BY_POLICY = "ESCALATED_BY_POLICY"  # core's cautious policy calls ("Escalated, not cleared")
_WEAK_TIER_FLAGS = ("DOB_FROM_SEARCH_RESULT", "NAME_FUZZY_MATCH", "NAME_FROM_SCREENED_ONLY",
                    "COUNTRY_FROM_SCREENED_ONLY", "NO_CUSTOMER_RECORD", ENGINES_DISAGREE)


def _enum(cls, value, default):
    try:
        return cls(value)
    except ValueError:
        return default


def merge_with_core(core, check, hit_id, alert_id):
    """Combine the core's decision with the independent modular check (see pipeline.py)."""
    from .contracts import HitDecision, Tier

    if core is None:  # core could not answer: never clear on the check alone
        if check is None:
            raise ValueError(f"no decision for {hit_id}")
        if check.decision is Decision.DIFFERENT_ENTITY:
            return HitDecision(hit_id, alert_id, Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA,
                               check.customer_record_id,
                               "Escalated, not cleared: the decision core gave no answer for this hit. "
                               "Reviewer: confirm the independent check's finding. " + check.reason,
                               check.evidence, Tier.HUMAN_REVIEW, (*check.flags, "CORE_MISSING"), check.trace)
        return check

    decision = _enum(Decision, core.get("decision"), Decision.REVIEW)
    reason_code = _enum(ReasonCode, core.get("reason_code"), ReasonCode.INSUFFICIENT_DATA)
    if reason_code not in REASONS_BY_DECISION[decision]:
        decision, reason_code = Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA
    reason = str(core.get("reason") or "")
    evidence = tuple(dict.fromkeys(str(e) for e in core.get("evidence") or () if e))
    customer = core.get("customer_record_id")
    flags: list[str] = []
    if reason.startswith("Escalated, not cleared"):
        flags.append(ESCALATED_BY_POLICY)

    signal = {Decision.DIFFERENT_ENTITY: Signal.CONTRADICTION, Decision.SAME_ENTITY: Signal.AGREEMENT}
    core_row = RuleResult(rule_id="CORE.aml", facet=Facet.CORE, signal=signal.get(decision, Signal.NEUTRAL),
                          summary=reason, reason=reason_code, evidence=evidence)
    trace = (core_row, *(check.trace if check else ()))

    if check is not None and check.decision is not decision:
        if decision is Decision.DIFFERENT_ENTITY and check.decision is Decision.SAME_ENTITY:
            flags.append(ENGINES_CONFLICT)
            decision, reason_code = Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA
            evidence = tuple(dict.fromkeys((*evidence, *check.evidence)))
            reason = ("Escalated, not cleared: the decision core found a contradiction but the independent "
                      f"rule check confirms the match ({check.reason_code.value}). Reviewer: compare the "
                      f"customer's verified identity with the hit. Core: {reason}")
        else:
            flags.append(ENGINES_DISAGREE)
        flags.extend(f for f in check.flags if f not in flags)
    elif check is not None:
        flags.extend(f for f in check.flags if f not in flags)

    if decision is Decision.REVIEW:
        tier = Tier.HUMAN_REVIEW
    elif any(f in _WEAK_TIER_FLAGS for f in flags):
        tier = Tier.BORDERLINE
    else:
        tier = Tier.AUTO_CLEAR if decision is Decision.DIFFERENT_ENTITY else Tier.AUTO_ESCALATE
    return HitDecision(hit_id=hit_id, alert_id=alert_id, decision=decision, reason_code=reason_code,
                       customer_record_id=customer, reason=reason, evidence=evidence, tier=tier,
                       flags=tuple(flags), trace=trace)
