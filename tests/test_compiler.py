from screening.compiler import Verdict, combine_persons, compile_person
from screening.contracts import Decision, Facet, ReasonCode, RuleResult, Signal


def r(facet, signal, reason=None):
    return RuleResult(rule_id=f"T.{facet.value}", facet=facet, signal=signal, summary="x", reason=reason)


AGREE_NAME = r(Facet.NAME, Signal.AGREEMENT)
AGREE_DOB = r(Facet.DOB, Signal.AGREEMENT)
AGREE_LOC = r(Facet.LOCATION, Signal.AGREEMENT)
LOC_MISMATCH = r(Facet.LOCATION, Signal.CONTRADICTION, ReasonCode.LOCATION_MISMATCH)
DOB_MISMATCH = r(Facet.DOB, Signal.CONTRADICTION, ReasonCode.DOB_MISMATCH)
TYPE_MISMATCH = r(Facet.TYPE, Signal.CONTRADICTION, ReasonCode.ENTITY_TYPE_MISMATCH)


def test_nothing_known_is_review():
    v = compile_person([], is_company=False, record_id=None)
    assert (v.decision, v.reason_code) == (Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA)


def test_contradiction_priority_is_fixed():
    v = compile_person([DOB_MISMATCH, TYPE_MISMATCH], is_company=False, record_id="C")
    assert v.reason_code is ReasonCode.ENTITY_TYPE_MISMATCH


def test_location_alone_never_clears_when_name_and_dob_agree():
    v = compile_person([AGREE_NAME, AGREE_DOB, LOC_MISMATCH], is_company=False, record_id="C")
    assert v.decision is Decision.REVIEW


def test_person_confirm_levels():
    full = compile_person([AGREE_NAME, AGREE_DOB, AGREE_LOC], is_company=False, record_id="C")
    assert full.reason_code is ReasonCode.NAME_DOB_LOCATION_MATCH
    nd = compile_person([AGREE_NAME, AGREE_DOB], is_company=False, record_id="C")
    assert nd.reason_code is ReasonCode.NAME_DOB_MATCH
    nl = compile_person([AGREE_NAME, AGREE_LOC], is_company=False, record_id="C")
    assert nl.decision is Decision.REVIEW  # persons need a DOB to confirm


def test_company_confirms_on_name_and_country():
    v = compile_person([AGREE_NAME, AGREE_LOC], is_company=True, record_id="C")
    assert v.reason_code is ReasonCode.NAME_LOCATION_MATCH


def test_ambiguous_persons():
    a = Verdict(Decision.DIFFERENT_ENTITY, ReasonCode.DOB_MISMATCH, (), "C1")
    b = Verdict(Decision.SAME_ENTITY, ReasonCode.NAME_DOB_MATCH, (), "C2")
    v = combine_persons([a, b])
    assert (v.decision, v.reason_code, v.customer_record_id) == (
        Decision.REVIEW,
        ReasonCode.AMBIGUOUS_CUSTOMER_RECORD,
        "AMBIGUOUS",
    )
    c = Verdict(Decision.DIFFERENT_ENTITY, ReasonCode.LOCATION_MISMATCH, (), "C2")
    same = combine_persons([a, c])
    assert same.decision is Decision.DIFFERENT_ENTITY
    assert same.customer_record_id == "AMBIGUOUS"
