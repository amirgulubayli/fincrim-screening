"""P6 time rules: deceased.py and temporal.py on tiny hand-built records."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from screening.contracts import Alert, Customer, Facet, Hit, ReasonCode, Signal
from screening.facts import DatedReport, HitFacts
from screening.loaders import Dataset
from screening.normalize.dates import PartialDate, parse_date
from screening.resolve import Person
from screening.rules import deceased, temporal
from screening.rules.base import Context


def _facts(deceased_on=None, report=None, life="") -> HitFacts:
    reports = ()
    if report is not None:
        d = parse_date(report)
        reports = (DatedReport(date=d, source="the example.org", line=f"According to x; {report}: y"),)
    return HitFacts(
        hit_id="H-1",
        names=("Test Person",),
        dobs_raw=(),
        dob_sources=(),
        countries_raw=(),
        deceased_date=str(deceased_on) if deceased_on else None,
        listing_phrase="a sanctioned individual",
        search_results=(),
        dated_reports=reports,
        earliest_dated_report=reports[0] if reports else None,
        deceased_on=parse_date(deceased_on) if deceased_on else None,
        is_deceased=bool(deceased_on) or life == "deceased",
    )


def _person(*dobs: str | None, etype: str = "Individual") -> Person:
    return Person(
        records=tuple(
            Customer(record_id=f"CUS-{i}", entity_type=etype, dob=dob) for i, dob in enumerate(dobs, 1)
        )
    )


def _ctx(facts: HitFacts, person=None, screened_at="2026-11-06", etype="Individual", alert_dob=None):
    alert = Alert(alert_id="A-1", screened_at=screened_at, entity_type=etype, dob=alert_dob)
    return Context(ds=Dataset(root=Path(".")), alert=alert, hit=Hit(hit_id="H-1"), facts=facts, person=person)


def _one(results):
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------- deceased


def test_death_before_screening_contradicts():
    r = _one(deceased.check(_ctx(_facts(deceased_on="2019-03-02"), _person("1980-01-01"))))
    assert r.signal is Signal.CONTRADICTION
    assert r.reason is ReasonCode.DECEASED and r.facet is Facet.DECEASED
    assert r.evidence == ("CUS-1",)
    assert r.summary == (
        "The hit's profile gives a date of death (2019-03-02) before the customer was screened (2026-11-06)."
    )


def test_year_only_death_in_screening_year_is_not_before():
    r = _one(deceased.check(_ctx(_facts(deceased_on="2026"), _person("1980"))))
    assert r.signal is Signal.NEUTRAL


def test_month_death_compares_only_shared_parts():
    assert _one(deceased.check(_ctx(_facts(deceased_on="2026-11"), _person()))).signal is Signal.NEUTRAL
    assert _one(deceased.check(_ctx(_facts(deceased_on="2026-10"), _person()))).signal is Signal.CONTRADICTION


def test_death_after_screening_is_neutral():
    assert _one(deceased.check(_ctx(_facts(deceased_on="2027-01-01"), _person()))).signal is Signal.NEUTRAL


def test_deceased_without_date_abstains():
    assert deceased.check(_ctx(_facts(life="deceased"), _person("1980"))) == []


def test_missing_screened_at_abstains():
    assert deceased.check(_ctx(_facts(deceased_on="2019-03-02"), _person(), screened_at=None)) == []


# ---------------------------------------------------------------- temporal (person)


def test_child_at_report_contradicts():
    r = _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("2012-05-01"))))
    assert r.signal is Signal.CONTRADICTION
    assert r.reason is ReasonCode.TEMPORAL_IMPOSSIBILITY and r.facet is Facet.TIME
    assert r.evidence == ("CUS-1",)
    assert r.summary == "The customer (born 2012-05-01, CUS-1) was 7 at the earliest dated report (2020-01-08)."


def test_year_only_dob_uses_oldest_possible_age():
    # born 2007 -> at most 13 on 2020-01-08 (if born Jan 1)
    r = _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("2007"))))
    assert r.signal is Signal.CONTRADICTION
    assert "at most 13" in r.summary


def test_year_only_dob_that_could_be_16_does_not_contradict():
    # born 2004: if born Jan 1 2004 they were 16 on 2020-01-08 -> not impossible
    assert _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("2004")))).signal is Signal.NEUTRAL


def test_sixteenth_birthday_edge():
    assert _one(temporal.check(_ctx(_facts(report="May 1, 2028"), _person("2012-05-01")))).signal is Signal.NEUTRAL
    assert (
        _one(temporal.check(_ctx(_facts(report="April 30, 2028"), _person("2012-05-01")))).signal
        is Signal.CONTRADICTION
    )


def test_adult_is_neutral():
    assert _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("1980-02-03")))).signal is Signal.NEUTRAL


def test_records_disagree_every_one_must_contradict():
    r = _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("2012", "1970"))))
    assert r.signal is Signal.NEUTRAL
    r = _one(temporal.check(_ctx(_facts(report="January 8, 2020"), _person("2012", "2010-06-01"))))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("CUS-1", "CUS-2")


def test_verified_dob_wins_and_is_cited():
    person = SimpleNamespace(
        records=(Customer(record_id="CUS-1", entity_type="Individual", dob="1970-01-01"),),
        record_ids=("CUS-1",),
        key="CUS-1",
        verified_dob_date=PartialDate(2007),
        verified_source_id="KYC-1",
    )
    r = _one(temporal.check(_ctx(_facts(report="January 8, 2020"), person)))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("KYC-1",)
    assert "KYC-1" in r.summary


def test_no_report_or_no_dob_abstains():
    assert temporal.check(_ctx(_facts(), _person("2012-05-01"))) == []
    assert temporal.check(_ctx(_facts(report="January 8, 2020"), _person(None))) == []
    assert temporal.check(_ctx(_facts(report="January 8, 2020"), None)) == []


# ---------------------------------------------------------------- temporal (company)


def test_company_incorporated_after_report_contradicts():
    ctx = _ctx(_facts(report="January 8, 2020"), _person("2021-03-01", etype="Company"), etype="Company")
    r = _one(temporal.check(ctx))
    assert r.signal is Signal.CONTRADICTION
    assert r.evidence == ("CUS-1",)


def test_company_incorporated_same_year_as_report_is_not_after():
    ctx = _ctx(_facts(report="January 8, 2020"), _person("2020", etype="Company"), etype="Company")
    assert _one(temporal.check(ctx)).signal is Signal.NEUTRAL


def test_company_falls_back_to_alert_dob():
    ctx = _ctx(_facts(report="January 8, 2020"), _person(None, etype="Company"), etype="Company", alert_dob="2022")
    assert _one(temporal.check(ctx)).signal is Signal.CONTRADICTION


def test_never_raises_on_garbage():
    facts = _facts(report="January 8, 2020")
    bad = SimpleNamespace(records=None, record_ids=None, key=None)
    assert temporal.check(_ctx(facts, bad)) == []
    assert deceased.check(_ctx(_facts(deceased_on="2019-03-02"), bad, screened_at="not a date")) == []
