import json

from screening.contracts import SUBMISSION_FIELDS, Decision, Tier
from screening.evaluate import score
from screening.pipeline import run
from screening.resolve import resolve_customer


def test_every_hit_answered_with_exact_submission_fields(mini):
    out = run(mini)
    assert [d.hit_id for d in out] == sorted(mini.hits)
    for d in out:
        assert tuple(d.submission()) == SUBMISSION_FIELDS


def test_type_mismatch_is_auto_cleared_with_customer_cited(mini):
    d = {x.hit_id: x for x in run(mini)}["H-1"]
    assert d.decision is Decision.DIFFERENT_ENTITY
    assert d.customer_record_id == "CUS-1"
    assert "CUS-1" in d.evidence
    assert d.tier is Tier.AUTO_CLEAR


def test_only_approved_kyc_is_evidence(mini):
    person = resolve_customer(mini, mini.alerts["A-1"]).persons[0]
    assert [k.check_id for k in person.kyc] == ["KYC-1"]


def test_joint_account_yields_two_candidates(mini):
    assert len(resolve_customer(mini, mini.alerts["A-2"]).persons) == 2


def test_runs_are_identical(mini):
    a = [json.dumps(d.full(), sort_keys=True) for d in run(mini)]
    b = [json.dumps(d.full(), sort_keys=True) for d in run(mini)]
    assert a == b


def test_baseline_never_clears_a_labelled_match(mini):
    result = score([d.submission() for d in run(mini)], mini.practice_labels)
    assert result["unsafe_clearances"] == 0
