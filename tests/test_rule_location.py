"""P6 Location / P7 country agreement. Tiny hand-built records only - never real data."""

from pathlib import Path
from types import SimpleNamespace

from screening.compiler import compile_person
from screening.contracts import (
    Alert,
    Customer,
    Decision,
    Facet,
    Hit,
    KycCheck,
    Note,
    ReasonCode,
    RuleResult,
    Signal,
)
from screening.facts import gather_hit_facts
from screening.loaders import Dataset
from screening.rules import location
from screening.rules.base import Context

DS = Dataset(root=Path("."))


def make_hit(**kw) -> Hit:
    raw = {"hit_id": "H-T1", "alert_id": "A-T1", "entity_type": "Individual", "name": "Lena Varga"}
    raw.update(kw)
    return Hit.from_raw(raw)


def claim(raw, source_id="CUS-1", kind="residence"):
    return SimpleNamespace(raw=raw, source_id=source_id, kind=kind, canonical=None)


def new_person(*claims, record_ids=("CUS-1",)):
    """Upgraded resolver shape: Person.countries holds CountryClaim objects."""
    return SimpleNamespace(countries=tuple(claims), record_ids=tuple(record_ids))


def old_person(records=(), kyc=(), notes=()):
    """Old resolver shape: no .countries, claims are rebuilt from records/KYC/notes."""
    return SimpleNamespace(
        records=tuple(records),
        kyc=tuple(kyc),
        notes=tuple(notes),
        record_ids=tuple(sorted(r.record_id for r in records)),
    )


def run(person, hit=None, alert=None):
    hit = hit or make_hit(countries=["United States"])
    alert = alert or Alert.from_raw({"alert_id": "A-T1", "customer_name": "Lena Varga"})
    ctx = Context(ds=DS, alert=alert, hit=hit, facts=gather_hit_facts(DS, hit), person=person)
    return location.check(ctx)


def only(results) -> RuleResult:
    assert len(results) == 1
    return results[0]


# ------------------------------------------------------------------ contradiction


def test_disjoint_countries_contradict_and_cite_customer_sources():
    person = new_person(
        claim("Netherlands", "CUS-1", "residence"),
        claim("NL", "CUS-1", "nationality"),
        claim("Netherlands", "KYC-9", "document"),
    )
    r = only(run(person))
    assert r.signal is Signal.CONTRADICTION
    assert r.reason is ReasonCode.LOCATION_MISMATCH
    assert r.facet is Facet.LOCATION
    assert r.evidence == ("CUS-1", "KYC-9")
    assert (
        r.summary == "The customer is linked only to Netherlands but the hit is linked only to United States."
    )


def test_every_country_of_every_record_counts():
    person = new_person(
        claim("Bangladesh", "CUS-1", "residence"),
        claim("United Kingdom", "CUS-2", "residence"),
        claim("Bangladeshi", "NOTE-1", "note_residence"),
        record_ids=("CUS-2", "CUS-1"),
    )
    r = only(run(person, make_hit(countries=["Pakistan"], nationality=["Pakistani"])))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("CUS-1", "CUS-2", "NOTE-1")
    assert "Bangladesh, United Kingdom" in r.summary


# ---------------------------------------------------------------------- agreement


def test_shared_country_agrees_and_cites_its_source():
    person = new_person(
        claim("Spain", "CUS-1", "residence"),
        claim("ES", "KYC-2", "document"),
        claim("France", "CUS-1", "nationality"),
    )
    r = only(run(person, make_hit(addresses=["Calle Mayor 1, Madrid, Spain"])))
    assert r.signal is Signal.AGREEMENT
    assert r.reason is None
    assert r.evidence == ("CUS-1", "KYC-2")
    assert r.summary == "Both the customer and the hit are linked to Spain."
    assert not r.weak


def test_codes_demonyms_and_territories_are_countries():
    person = new_person(claim("British", "CUS-1", "nationality"))
    r = only(run(person, make_hit(countries=["England"])))
    assert r.signal is Signal.AGREEMENT
    assert "United Kingdom" in r.summary


def test_one_shared_country_is_enough_even_with_other_differences():
    person = new_person(claim("Pakistan", "CUS-1"), claim("United Kingdom", "CUS-1", "nationality"))
    r = only(run(person, make_hit(countries=["Pakistan", "Afghanistan"])))
    assert r.signal is Signal.AGREEMENT


# ------------------------------------------------------------- abstain (R5, R7)


def test_unknown_customer_value_blocks_contradiction():
    person = new_person(claim("Netherlands"), claim("Atlantis", "NOTE-1", "note_residence"))
    r = only(run(person))
    assert r.signal is Signal.NEUTRAL
    assert "Atlantis" in r.summary


def test_ambiguous_hit_value_blocks_contradiction():
    r = only(
        run(new_person(claim("Netherlands")), make_hit(countries=["United States"], citizenship=["Congo"]))
    )
    assert r.signal is Signal.NEUTRAL


def test_hit_address_ending_in_a_city_blocks_contradiction():
    hit = make_hit(countries=["United States"], addresses=["12 Tverskaya St, Moscow"])
    assert only(run(new_person(claim("Netherlands")), hit)).signal is Signal.NEUTRAL


def test_international_europe_and_bracket_tags_are_not_countries():
    hit = make_hit(countries=["International", "Europe", "United States [Associated]"])
    r = only(run(new_person(claim("Spain")), hit))
    assert r.signal is Signal.CONTRADICTION
    assert r.summary.endswith("the hit is linked only to United States.")
    assert only(run(new_person(claim("USA")), hit)).signal is Signal.AGREEMENT


def test_hit_without_countries_never_contradicts():
    r = only(run(new_person(claim("Spain")), make_hit(countries=["International"])))
    assert r.signal is Signal.NEUTRAL


def test_customer_without_countries_never_contradicts():
    r = only(run(new_person(claim(""), claim("Europe"))))
    assert r.signal is Signal.NEUTRAL


def test_full_value_rescues_a_comma_country():
    hit = make_hit(countries=["Korea, Republic of"])
    assert only(run(new_person(claim("South Korea")), hit)).signal is Signal.AGREEMENT
    assert only(run(new_person(claim("Japan")), hit)).signal is Signal.CONTRADICTION


# ------------------------------------------------------------------ no customer


def test_no_person_never_contradicts():
    alert = Alert.from_raw({"alert_id": "A-T1", "country": "Spain"})
    r = only(run(None, alert=alert))
    assert r.signal is Signal.NEUTRAL


def test_no_person_screened_country_agrees_weakly():
    alert = Alert.from_raw({"alert_id": "A-T1", "country": "US"})
    r = only(run(None, alert=alert))
    assert r.signal is Signal.AGREEMENT
    assert r.weak and r.weak_why == location.WEAK_SCREENED_ONLY


# ------------------------------------------------------------- old resolver shape


def _cus(rid, residence=None, nationality=()):
    return Customer.from_raw(
        {"record_id": rid, "residence_country": residence, "nationality": list(nationality)}
    )


def test_fallback_builds_claims_from_records_and_kyc():
    person = old_person(
        records=[_cus("CUS-2", "Germany"), _cus("CUS-1", None, ["German"])],
        kyc=[KycCheck.from_raw({"check_id": "KYC-5", "document_country": "DEU", "result": "approved"})],
    )
    r = only(run(person, make_hit(countries=["Switzerland"])))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("CUS-1", "CUS-2", "KYC-5")


def test_fallback_counts_countries_named_in_notes():
    person = old_person(
        records=[_cus("CUS-1", "Germany")],
        notes=[
            Note.from_raw({"note_id": "NOTE-1", "text": "Customer lived in the United States until 2019."})
        ],
    )
    r = only(run(person, make_hit(countries=["United States"])))
    assert r.signal is Signal.AGREEMENT
    assert r.evidence == ("NOTE-1",)
    assert r.weak and r.weak_why == location.WEAK_NOTE_TEXT


# ----------------------------------------------------------------- safety, misc


def test_location_only_contradiction_with_name_and_dob_agreement_is_review():
    loc = only(run(new_person(claim("Netherlands"))))
    name = RuleResult("P6.name", Facet.NAME, Signal.AGREEMENT, "names agree")
    dob = RuleResult("P6.dob", Facet.DOB, Signal.AGREEMENT, "dobs agree")
    v = compile_person([name, dob, loc], is_company=False, record_id="CUS-1")
    assert v.decision is Decision.REVIEW


def test_never_raises_on_messy_person():
    assert run(SimpleNamespace(countries=[object(), None], record_ids=None)) == []


def test_deterministic_regardless_of_claim_order():
    a = new_person(
        claim("Peru", "CUS-2"),
        claim("United States", "CUS-1"),
        claim("PE", "KYC-1", "document"),
        record_ids=("CUS-2", "CUS-1"),
    )
    b = new_person(
        claim("PE", "KYC-1", "document"),
        claim("United States", "CUS-1"),
        claim("Peru", "CUS-2"),
        record_ids=("CUS-1", "CUS-2"),
    )
    hit = make_hit(countries=["United Kingdom"])
    assert run(a, hit) == run(b, hit)
