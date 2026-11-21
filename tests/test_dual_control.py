"""Dual control: the decision core decides, the independent check can only make it safer."""

from screening.compiler import ENGINES_CONFLICT, ENGINES_DISAGREE, merge_with_core
from screening.contracts import Decision, HitDecision, ReasonCode, Tier


def check(decision, code, flags=()):
    return HitDecision("H-1", "A-1", decision, code, "CUS-1", "check reason", ("CUS-1",), Tier.HUMAN_REVIEW,
                       tuple(flags), ())


def core(decision, code, reason="core reason"):
    return {"hit_id": "H-1", "alert_id": "A-1", "decision": decision, "reason_code": code,
            "customer_record_id": "CUS-1", "reason": reason, "evidence": ["KYC-1"]}


def test_core_clear_vs_check_confirm_becomes_review():
    d = merge_with_core(core("DIFFERENT_ENTITY", "LOCATION_MISMATCH"),
                        check(Decision.SAME_ENTITY, ReasonCode.NAME_DOB_MATCH), "H-1", "A-1")
    assert d.decision is Decision.REVIEW and d.tier is Tier.HUMAN_REVIEW
    assert ENGINES_CONFLICT in d.flags
    assert d.reason.startswith("Escalated, not cleared")


def test_other_disagreement_keeps_core_but_goes_to_four_eyes():
    d = merge_with_core(core("DIFFERENT_ENTITY", "DOB_MISMATCH"),
                        check(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA), "H-1", "A-1")
    assert d.decision is Decision.DIFFERENT_ENTITY
    assert ENGINES_DISAGREE in d.flags and d.tier is Tier.BORDERLINE


def test_agreement_is_automated():
    d = merge_with_core(core("DIFFERENT_ENTITY", "DOB_MISMATCH"),
                        check(Decision.DIFFERENT_ENTITY, ReasonCode.DOB_MISMATCH), "H-1", "A-1")
    assert d.tier is Tier.AUTO_CLEAR and d.trace[0].rule_id == "CORE.aml"


def test_missing_core_never_clears_on_check_alone():
    d = merge_with_core(None, check(Decision.DIFFERENT_ENTITY, ReasonCode.DOB_MISMATCH), "H-1", "A-1")
    assert d.decision is Decision.REVIEW and "CORE_MISSING" in d.flags


def test_invalid_core_reason_code_falls_back_to_review():
    d = merge_with_core(core("DIFFERENT_ENTITY", "NAME_DOB_MATCH"),
                        check(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA), "H-1", "A-1")
    assert (d.decision, d.reason_code) == (Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA)


def test_policy_escalation_is_flagged():
    d = merge_with_core(core("REVIEW", "INSUFFICIENT_DATA", "Escalated, not cleared: x. Reviewer: y."),
                        check(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA), "H-1", "A-1")
    assert "ESCALATED_BY_POLICY" in d.flags
