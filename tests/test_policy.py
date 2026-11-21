from screening.contracts import Decision, HitDecision, ReasonCode, Tier
from screening.policy import BY_ID, REGISTRY, policies_for, registry


def _d(decision, code, *, customer="CUS-1", flags=(), evidence=("CUS-1",), reason="", hit_id="H-X"):
    return HitDecision(hit_id, "A-X", decision, code, customer, reason, tuple(evidence), Tier.HUMAN_REVIEW,
                       tuple(flags))


def test_registry_has_all_families_with_unique_ids():
    ids = [p.id for p in REGISTRY]
    assert len(ids) == len(set(ids))
    for pid in [f"P{i}" for i in range(1, 10)] + [f"HOFC-{i}" for i in range(1, 10)] + ["DC-1", "DC-2"]:
        assert pid in BY_ID, pid
    for row in registry():
        assert set(row) == {"id", "kind", "title", "effect", "implemented_by", "triggers"}
        assert row["kind"] in {"sop", "hofc", "dual_control"}
        assert row["implemented_by"]


def test_clearance_maps_to_p6_and_verified_name():
    ids = policies_for(_d(Decision.DIFFERENT_ENTITY, ReasonCode.NAME_MISMATCH_VERIFIED, evidence=("KYC-1",)))
    assert ids[:1] == ["P4"] and "P6" in ids and "P8" in ids


def test_reviewer_escalations_map_to_hofc():
    r = ("Escalated, not cleared: the customer is linked to Serbia and the hit to Croatia. These countries "
         "were part of one state, Yugoslavia, ... Reviewer: check the dates.")
    assert "HOFC-3" in policies_for(_d(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, reason=r))
    r = "Escalated, not cleared: ... only country link is a business address in X. Reviewer: check."
    assert "HOFC-4" in policies_for(_d(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, reason=r))
    r = "Escalated, not cleared: ... shares only one name part, 'Smith', with ... Reviewer: compare."
    assert "HOFC-1" in policies_for(_d(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, reason=r))


def test_dual_control_flags_and_ambiguous_clear():
    assert "DC-1" in policies_for(_d(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, flags=("ENGINES_CONFLICT",)))
    assert "DC-2" in policies_for(_d(Decision.SAME_ENTITY, ReasonCode.NAME_DOB_MATCH, flags=("ENGINES_DISAGREE",)))
    ids = policies_for(_d(Decision.DIFFERENT_ENTITY, ReasonCode.DOB_MISMATCH, customer="AMBIGUOUS"))
    assert {"P3", "P6", "HOFC-5"} <= set(ids)
    assert "P2" in policies_for(_d(Decision.REVIEW, ReasonCode.INSUFFICIENT_DATA, customer=None))


def test_dict_input_and_deterministic_registry_order():
    d = _d(Decision.DIFFERENT_ENTITY, ReasonCode.LOCATION_MISMATCH, evidence=("CUS-1", "NOTE-1"))
    ids = policies_for(d)
    assert ids == policies_for(d.full())
    assert "HOFC-7" in ids
    order = [p.id for p in REGISTRY]
    assert ids == sorted(ids, key=order.index)
