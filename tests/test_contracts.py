from screening.contracts import REASONS_BY_DECISION, Alert, Decision, ReasonCode, as_list


def test_as_list_accepts_lists_and_semicolon_strings():
    assert as_list("a; b ;") == ("a", "b")
    assert as_list(["a", None, " b "]) == ("a", "b")
    assert as_list(None) == ()


def test_unknown_fields_are_kept_not_fatal():
    alert = Alert.from_raw({"alert_id": "A-9", "brand_new_column": 1, "hit_ids": "H-1"})
    assert alert.hit_ids == ("H-1",)
    assert alert.extra == {"brand_new_column": 1}


def test_every_reason_code_belongs_to_exactly_one_decision():
    seen = [code for codes in REASONS_BY_DECISION.values() for code in codes]
    assert sorted(seen) == sorted(ReasonCode)
    assert set(REASONS_BY_DECISION) == set(Decision)
