"""DOB rule (SOP P6 DOB / P7 agreement). Tiny hand-built records only - never real data.

Covers the engine-plan guards R1 (day-first slash dates, unreadable abstains),
R2 (year-only stays partial), R3 (age gives two years), R4 (search results weak,
only without other DOBs) and R14 (company DOB only from the `dobs` field).
"""

from pathlib import Path
from types import SimpleNamespace

from screening.contracts import Alert, Customer, Facet, Hit, ReasonCode, Signal
from screening.facts import WEAK_SEARCH_RESULT, DobCandidate, HitFacts
from screening.loaders import Dataset
from screening.normalize.dates import PartialDate
from screening.rules import dob
from screening.rules.base import Context

DS = Dataset(root=Path("."))


def cand(years, date=None, source="dobs", evidence="H-T1", from_age=False):
    weak = source == "search_result"
    return DobCandidate(tuple(years), date, str(date or years), source, evidence, from_age, weak)


def dobs_cand(raw: str) -> DobCandidate:
    from screening.normalize.dates import parse_date

    d = parse_date(raw)
    return DobCandidate((d.year,) if d else (), d, raw, "dobs", "H-T1")


def age_cand(lo: int, source="profile_notes", evidence="H-T1") -> DobCandidate:
    return cand((lo, lo + 1), None, source, evidence, from_age=True)


def facts(*cands, unparsed=(), company=False) -> HitFacts:
    return HitFacts(
        hit_id="H-T1", names=("Lena Varga",), dobs_raw=(), dob_sources=(), countries_raw=(),
        deceased_date=None, listing_phrase="a sanctioned individual", search_results=(),
        is_company=company, dob_candidates=tuple(cands),
        dob_from_search_only=bool(cands) and all(c.weak for c in cands),
        notes_dob_unparsed=tuple(unparsed),
    )


def record(rid="CUS-1", dob_raw=None, etype="Individual") -> Customer:
    return Customer.from_raw({"record_id": rid, "entity_type": etype, "dob": dob_raw})


def person(*records, verified=None):
    """New-style Person (resolver #4): .verified with source_id / dob / dob_raw."""
    return SimpleNamespace(
        records=records or (record(),),
        record_ids=tuple(sorted(r.record_id for r in (records or (record(),)))),
        verified=verified,
    )


def verified(source_id="KYC-1", raw=None):
    from screening.normalize.dates import parse_date

    return SimpleNamespace(source_id=source_id, kind="kyc_check", dob_raw=raw, dob=parse_date(raw))


def ctx(f: HitFacts, p, etype="Individual") -> Context:
    alert = Alert.from_raw({"alert_id": "A-T1", "entity_type": etype})
    hit = Hit.from_raw({"hit_id": "H-T1", "alert_id": "A-T1", "entity_type": etype, "name": "Lena Varga"})
    return Context(ds=DS, alert=alert, hit=hit, facts=f, person=p)


def run(f, p, etype="Individual"):
    out = dob.check(ctx(f, p, etype))
    assert len(out) <= 1
    for r in out:
        assert r.rule_id == "P6.dob" and r.facet is Facet.DOB
    return out[0] if out else None


# ----------------------------------------------------------------- agreement (P7)


def test_verified_dob_agrees_with_age_years():
    p = person(record("CUS-1", "1970-01-01"), verified=verified("NOTE-14", "1968-03-05"))
    r = run(facts(age_cand(1967)), p)
    assert r.signal is Signal.AGREEMENT
    assert r.evidence == ("NOTE-14",)  # verified beats declared; hit id not cited
    assert r.summary == "The customer's date of birth 1968-03-05 (NOTE-14) agrees with the hit's 1967 or 1968."
    assert not r.weak


def test_year_only_hit_agrees_with_full_customer_date():
    r = run(facts(dobs_cand("1994"), dobs_cand("1995")), person(verified=verified("KYC-9", "1994-11-01")))
    assert r.signal is Signal.AGREEMENT
    assert "agrees with the hit's 1994, 1995" in r.summary


def test_month_agreement_partial():
    r = run(facts(dobs_cand("1968-02")), person(verified=verified("NOTE-4", "17/02/1968")))
    assert r.signal is Signal.AGREEMENT
    assert "1968-02-17 (NOTE-4) agrees with the hit's 1968-02" in r.summary


def test_record_dob_used_when_no_verified():
    r = run(facts(dobs_cand("1980")), person(record("CUS-7", "3 March 1980")))
    assert r.signal is Signal.AGREEMENT
    assert r.evidence == ("CUS-7",)


def test_search_result_agreement_is_weak_and_cites_sr():
    r = run(facts(age_cand(1985, "search_result", "SR-77")), person(verified=verified("NOTE-2", "1985-09-12")))
    assert r.signal is Signal.AGREEMENT
    assert r.evidence == ("NOTE-2", "SR-77")
    assert r.weak and r.weak_why == WEAK_SEARCH_RESULT


# -------------------------------------------------------------- contradiction (P6)


def test_listed_date_contradicted():
    r = run(facts(dobs_cand("1962-09-10")), person(verified=verified("KYC-2", "1965-03-28")))
    assert r.signal is Signal.CONTRADICTION and r.reason is ReasonCode.DOB_MISMATCH
    assert r.evidence == ("KYC-2",)
    assert r.summary == (
        "The customer's date of birth is 1965-03-28 (KYC-2) but the hit's listed date of birth is 1962-09-10."
    )


def test_notes_age_contradicted():
    r = run(facts(age_cand(1976)), person(verified=verified("NOTE-8", "1979-11-08")))
    assert r.signal is Signal.CONTRADICTION
    assert r.summary.endswith("but the hit's profile notes put its birth year at 1976 or 1977.")


def test_search_result_contradiction_is_weak():
    r = run(facts(age_cand(1972, "search_result", "SR-A8")), person(verified=verified("NOTE-C", "1979-04-17")))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("NOTE-C", "SR-A8")
    assert r.weak and r.weak_why == WEAK_SEARCH_RESULT
    assert "search results put its birth year at 1972 or 1973" in r.summary


def test_same_year_different_month_contradicts():
    r = run(facts(dobs_cand("1980-04-02")), person(verified=verified("KYC-3", "1980-06-02")))
    assert r.signal is Signal.CONTRADICTION


# ------------------------------------------------------------------- safety guards


def test_r3_age_years_one_off_is_not_a_contradiction():
    # aged-at-date -> 1976/1977; customer 1978 is 1 from 1977 -> neutral, never a clear
    r = run(facts(age_cand(1976)), person(verified=verified("KYC-4", "1978-05-05")))
    assert r.signal is Signal.NEUTRAL


def test_one_year_apart_is_neutral():
    r = run(facts(dobs_cand("1981")), person(verified=verified("KYC-4", "1980-12-31")))
    assert r.signal is Signal.NEUTRAL


def test_one_agreeing_date_prevents_contradiction():
    r = run(facts(dobs_cand("1950"), dobs_cand("1980")), person(verified=verified("KYC-5", "1980-01-02")))
    assert r.signal is Signal.AGREEMENT


def test_r2_year_only_customer_never_contradicts_on_month():
    r = run(facts(dobs_cand("1980-04-02")), person(verified=verified("KYC-6", "1980")))
    assert r.signal is Signal.AGREEMENT


def test_r1_slash_dates_are_day_first():
    # 05/03/1980 is 5 March; the hit says 1980-03-05 -> agreement (an MM/DD read would clear)
    r = run(facts(dobs_cand("1980-03-05")), person(verified=verified("KYC-7", "05/03/1980")))
    assert r.signal is Signal.AGREEMENT


def test_r1_unreadable_verified_dob_abstains_without_record_fallback():
    p = person(record("CUS-1", "1950-01-01"), verified=verified("KYC-8", "circa 1980"))
    r = run(facts(dobs_cand("1990")), p)
    assert r.signal is Signal.NEUTRAL


def test_unreadable_hit_dobs_value_blocks_contradiction():
    r = run(facts(dobs_cand("1950"), dobs_cand("1960s")), person(verified=verified("KYC-9", "1990-01-01")))
    assert r.signal is Signal.NEUTRAL


def test_unparsed_notes_sentence_blocks_contradiction():
    f = facts(age_cand(1950), unparsed=("He was born in the early fifties.",))
    r = run(f, person(verified=verified("KYC-9", "1990-01-01")))
    assert r.signal is Signal.NEUTRAL


def test_r4_search_results_ignored_when_hit_gives_another_dob():
    f = facts(dobs_cand("1990"), age_cand(1950, "search_result", "SR-1"))
    r = run(f, person(verified=verified("KYC-1", "1990-02-02")))
    assert r.signal is Signal.AGREEMENT and not r.weak
    assert "SR-1" not in r.evidence


def test_missing_customer_dob_is_neutral():
    r = run(facts(dobs_cand("1990")), person(record("CUS-1", None)))
    assert r.signal is Signal.NEUTRAL


def test_hit_without_dob_is_neutral():
    r = run(facts(), person(verified=verified("KYC-1", "1990-02-02")))
    assert r.signal is Signal.NEUTRAL


def test_no_person_abstains():
    assert run(facts(dobs_cand("1990")), None) is None


def test_records_disagreeing_do_not_contradict_when_one_is_unknown():
    p = person(record("CUS-1", "1950-01-01"), record("CUS-2", "not a date"))
    r = run(facts(dobs_cand("1990")), p)
    assert r.signal is Signal.NEUTRAL


def test_records_disagreeing_do_not_confirm():
    p = person(record("CUS-1", "1990-01-01"), record("CUS-2", "1950-01-01"))
    r = run(facts(dobs_cand("1990")), p)
    assert r.signal is Signal.NEUTRAL


# ------------------------------------------------------------------- companies (R14)


def test_company_only_dobs_field_counts():
    f = facts(age_cand(1950, "profile_notes"), company=True)
    p = person(record("CUS-C", "2001-05-05", etype="Company"))
    r = run(f, p, etype="Company")
    assert r.signal is Signal.NEUTRAL


def test_company_incorporation_contradicted_by_dobs():
    f = facts(dobs_cand("1950"), company=True)
    p = person(record("CUS-C", "2001-05-05", etype="Company"))
    r = run(f, p, etype="Company")
    assert r.signal is Signal.CONTRADICTION
    assert "incorporation date" in r.summary


# ----------------------------------------------------------------- old Person shape


def test_old_person_shape_uses_latest_kyc():
    kyc = SimpleNamespace(check_id="KYC-OLD", verified_dob="1968-03-05")
    p = SimpleNamespace(records=(record("CUS-1", "1970-01-01"),), latest_kyc=kyc)
    r = run(facts(age_cand(1967)), p)
    assert r.signal is Signal.AGREEMENT and r.evidence == ("KYC-OLD",)


def test_old_person_shape_never_clears():
    # Without P4 identities a note may hold a different verified DOB: agree, never clear.
    p = SimpleNamespace(records=(record("CUS-1", "1963-12-02"),), latest_kyc=None)
    r = run(facts(dobs_cand("1968-02")), p)
    assert r.signal is Signal.NEUTRAL
    assert "verified identity is not available" in r.summary


def test_never_raises_on_garbage():
    p = SimpleNamespace(records=None, verified=SimpleNamespace(source_id=None, dob=object(), dob_raw=5))
    assert isinstance(dob.check(ctx(facts(dobs_cand("1990")), p)), list)


def test_deterministic():
    f = facts(dobs_cand("1962-09-10"), age_cand(1950))
    p = person(verified=verified("KYC-2", "1990-03-28"))
    assert dob.check(ctx(f, p)) == dob.check(ctx(f, p))


def test_partial_date_type_from_verified():
    p = person(verified=SimpleNamespace(source_id="KYC-P", dob=PartialDate(1980, 3), dob_raw="March 1980"))
    r = run(facts(dobs_cand("1980-03-14")), p)
    assert r.signal is Signal.AGREEMENT


# ------------------------------------------- HoFC policy calls (all DOBs, partial, news ages)


def person_with(*records, verified=None, identities=()):
    p = person(*records, verified=verified)
    p.identities = tuple(identities)
    return p


def test_record_dob_that_does_not_contradict_blocks_clearance():
    # Verified DOB contradicts the hit, but a customer record holds the hit's own date.
    p = person(record("CUS-1", "1962-09-10"), verified=verified("KYC-2", "1965-03-28"))
    r = run(facts(dobs_cand("1962-09-10")), p)
    assert r.signal is Signal.NEUTRAL and r.reason is None
    assert "another date of birth the bank holds" in r.summary


def test_every_held_dob_contradicted_clears_and_cites_verified():
    p = person(record("CUS-1", "1966-01-01"), verified=verified("KYC-2", "1965-03-28"))
    r = run(facts(dobs_cand("1962-09-10")), p)
    assert r.signal is Signal.CONTRADICTION and r.reason is ReasonCode.DOB_MISMATCH
    assert r.evidence == ("KYC-2",)


def test_unreadable_record_dob_blocks_clearance():
    p = person(record("CUS-1", "31/02/1970"), verified=verified("KYC-2", "1965-03-28"))
    r = run(facts(dobs_cand("1990-01-01")), p)
    assert r.signal is Signal.NEUTRAL and "could not be read" in r.summary


def test_other_approved_check_dob_blocks_clearance():
    older = SimpleNamespace(source_id="NOTE-OLD", dob_raw="1990-02-03", dob=None)
    p = person_with(verified=verified("KYC-2", "1965-03-28"), identities=(older,))
    r = run(facts(dobs_cand("1990")), p)
    assert r.signal is Signal.NEUTRAL


def test_unreadable_other_approved_check_dob_blocks_clearance():
    older = SimpleNamespace(source_id="NOTE-OLD", dob_raw="sometime in the 80s", dob=None)
    p = person_with(verified=verified("KYC-2", "1965-03-28"), identities=(older,))
    r = run(facts(dobs_cand("1990")), p)
    assert r.signal is Signal.NEUTRAL and "NOTE-OLD" in r.summary


def test_name_plus_partial_dob_confirms():
    # Age at a date gives 1987 or 1988; a full, year-month or year-only DOB in 1988 agrees.
    for raw in ("1988-04-28", "1988-04", "1988"):
        r = run(facts(age_cand(1987)), person(verified=verified("KYC-P", raw)))
        assert r.signal is Signal.AGREEMENT, raw


def test_news_report_age_is_ignored():
    news = DobCandidate((1974, 1975), None, "According to Daily Wire; May 1, 2020: Lena Varga, 45, was held.",
                        "profile_notes", "H-T1", True)
    r = run(facts(news), person(verified=verified("KYC-1", "1990-02-02")))
    assert r.signal is Signal.NEUTRAL and r.reason is None
    assert "the hit gives none" in r.summary


def test_ages_in_a_news_report_first_paragraph_are_ignored():
    f = facts(age_cand(1974))
    hit = Hit.from_raw({"hit_id": "H-T1", "alert_id": "A-T1", "entity_type": "Individual", "name": "Lena Varga",
                        "profile_notes": "According to Daily Wire; May 1, 2020: Lena Varga, 45, was held."})
    alert = Alert.from_raw({"alert_id": "A-T1", "entity_type": "Individual"})
    c = Context(ds=DS, alert=alert, hit=hit, facts=f, person=person(verified=verified("KYC-1", "1990-02-02")))
    (r,) = dob.check(c)
    assert r.signal is Signal.NEUTRAL and r.reason is None
