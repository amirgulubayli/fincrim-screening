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

from .contracts import CUSTOMER_AMBIGUOUS, Decision, Facet, ReasonCode, RuleResult, Signal

CONTRADICTION_PRIORITY = (
    ReasonCode.ENTITY_TYPE_MISMATCH,
    ReasonCode.DECEASED,
    ReasonCode.TEMPORAL_IMPOSSIBILITY,
    ReasonCode.DOB_MISMATCH,
    ReasonCode.NAME_MISMATCH_VERIFIED,
    ReasonCode.LOCATION_MISMATCH,
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
