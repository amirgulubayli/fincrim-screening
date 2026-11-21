"""Hit facts (SOP principle 5). Tiny hand-built records only - never real data."""

from pathlib import Path

from screening.contracts import Hit, SearchResult
from screening.facts import (
    DEFAULT_LISTING_PHRASE,
    WEAK_SEARCH_RESULT,
    dated_reports,
    dobs_from_text,
    first_paragraph,
    gather_hit_facts,
    listing_phrase,
)
from screening.loaders import Dataset
from screening.normalize.dates import PartialDate


def make_hit(**kw) -> Hit:
    raw = {"hit_id": "H-T1", "alert_id": "A-T1", "entity_type": "Individual", "name": "Lena Varga"}
    raw.update(kw)
    return Hit.from_raw(raw)


def make_ds(*results: dict) -> Dataset:
    ds = Dataset(root=Path("."))
    for r in results:
        sr = SearchResult.from_raw(r)
        ds.search_results[sr.result_id] = sr
    ds.build_indexes()
    return ds


def sr(rid: str, snippet: str, query: str = "lena varga") -> dict:
    return {"result_id": rid, "query": query, "title": "Lena Varga - news", "snippet": snippet}


def years(facts, source=None):
    return sorted({y for c in facts.dob_candidates if source in (None, c.source) for y in c.years})


# ------------------------------------------------------------------- first paragraph


def test_first_paragraph_stops_at_blank_line():
    text = "\nLine one.\nLine two.\n   \nSecond paragraph, born in 1950."
    assert first_paragraph(text) == "Line one.\nLine two."


def test_dob_sentence_outside_first_paragraph_is_ignored():
    hit = make_hit(profile_notes="Career:\nMinister (2001 - 2005).\n\nLena Varga was born in 1950.")
    assert gather_hit_facts(make_ds(), hit).dob_candidates == ()


# ---------------------------------------------------------------- DOB from profile


def test_born_in_year_and_born_on_full_date():
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Lena Varga was born in 1952."))
    assert years(f) == [1952] and f.dob_sources == ("profile_notes",)
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Lena Varga was born on 17 July 1939."))
    assert f.dob_candidates[0].date == PartialDate(1939, 7, 17)
    assert f.dobs_raw == ("1939-07-17",)


def test_birth_date_must_follow_the_birth_word():
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Lena Varga was born in Lagos, Nigeria, in 1960."))
    assert years(f) == [1960]
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Date of birth: 12 March 1960."))
    assert f.dob_candidates[0].date == PartialDate(1960, 3, 12)
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Lena Varga was born circa 1960."))
    assert years(f) == [1959, 1960, 1961] and f.dob_candidates[0].date is None
    # the date belongs to another clause, not to "born"
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Born in Lagos, Lena Varga was 45 in March 2019."))
    assert years(f) == [1973, 1974] and f.dob_candidates[0].from_age


def test_age_at_date_gives_two_years_in_many_wordings():
    wordings = [
        "Formal charges were brought against Lena Varga, aged 45, in March 2019.",
        "Investigators questioned Lena Varga in March 2019, when she reached the age of 45.",
        "In March 2019, investigators questioned 45-year-old Lena Varga.",
        "Questioned in March 2019, Lena Varga was 45 years old.",
        "Lena Varga reached 45 years of age by March 2019, the time she was charged.",
        "The age of Lena Varga was 45 when she was questioned in March 2019.",
        "Lena Varga, at age 45, had charges filed against her in March 2019.",
        "At the time Lena Varga faced charges in March 2019, she was 45.",
        "Local reports from March 2019 indicate Lena Varga was 45 at the time of her charge.",
    ]
    for text in wordings:
        f = gather_hit_facts(make_ds(), make_hit(profile_notes=text))
        assert years(f) == [1973, 1974], text
        assert all(c.from_age and not c.weak for c in f.dob_candidates)


def test_month_first_slash_dob_in_profile_is_read_when_unambiguous():
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="Name: Varga, Lena\nDOB: 6/20/1955\nSex: F"))
    assert f.dob_candidates[0].date == PartialDate(1955, 6, 20)
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="DOB: 05/06/1955"))
    assert f.dob_candidates[0].date == PartialDate(1955)  # order unknown: keep the year only


def test_relatives_and_non_ages_do_not_give_a_dob():
    for text in (
        "Her son was born in 1990.",
        "In March 2019 the court imposed an 18-year prison sentence on Lena Varga.",
        "In June 2014 the penalty was 50 in total.",
    ):
        assert gather_hit_facts(make_ds(), make_hit(profile_notes=text)).dob_candidates == (), text


def test_news_report_paragraph_needs_the_hit_name_for_an_age():
    text = "According to the daily.example; May 02, 2019: Police arrested Omar Diaz, 32, in May 2019."
    f = gather_hit_facts(make_ds(), make_hit(profile_notes=text))
    assert f.dob_candidates == ()
    assert f.notes_dob_unparsed == ()  # no age wording; the apposition alone is not flagged


def test_unreadable_birth_wording_is_flagged_only_without_dobs():
    hit = make_hit(profile_notes="Lena Varga turned forty in 1990, records say she was born around then.")
    f = gather_hit_facts(make_ds(), hit)
    # "born around then" has no date, but the sentence names a year: flagged for the DOB rule
    assert f.notes_dob_unparsed and f.dob_candidates == ()
    f2 = gather_hit_facts(make_ds(), make_hit(dobs="1950", profile_notes=hit.profile_notes))
    assert f2.notes_dob_unparsed == ()


def test_dobs_field_kept_raw_and_parallel_sources():
    f = gather_hit_facts(make_ds(), make_hit(dobs="1983; 1982"))
    assert f.dobs_raw == ("1983", "1982")
    assert f.dob_sources == ("dobs", "dobs")
    assert [c.evidence for c in f.dob_candidates] == ["H-T1", "H-T1"]


# ------------------------------------------------------------------ search results


def test_listing_phrase_from_primary_else_first_category():
    assert listing_phrase(make_hit(primary_category="PEP", categories="Sanctions")) == "a politically exposed person"
    assert listing_phrase(make_hit(categories="Sanctions; Enforcement")) == "a sanctioned individual"
    assert listing_phrase(make_hit(primary_category="Registration")) == DEFAULT_LISTING_PHRASE
    assert listing_phrase(make_hit()) == DEFAULT_LISTING_PHRASE


def test_search_result_needs_phrase_and_country_and_is_weak():
    ds = make_ds(
        sr("SR-1", "May 2019 news reports profiled 33 year old Lena Varga, who is a politically exposed person in Brazil."),
        sr("SR-2", "Lena Varga, 50, a politically exposed person in Denmark, was listed in June 2010."),  # wrong country
        sr("SR-3", "In August 2012, Lena Varga, 41, a taxi driver in Brazil, received an award."),  # wrong kind
        sr("SR-4", "Lena Varga, 29, a sanctioned individual in Brazil, was named in May 2001."),  # wrong phrase
    )
    hit = make_hit(categories="PEP", addresses="Ribeirao Pires, Sao Paulo, Brazil [Residency]")
    f = gather_hit_facts(ds, hit)
    assert [r.result_id for r in f.relevant_search_results] == ["SR-1"]
    assert years(f) == [1985, 1986]
    assert f.dob_sources == ("SR-1", "SR-1")
    assert f.dob_from_search_only and f.dob_candidates[0].weak
    assert f.dob_candidates[0].weak_why == WEAK_SEARCH_RESULT
    assert f.dob_evidence == ("SR-1",)


def test_search_result_country_by_demonym_or_resident():
    ds = make_ds(
        sr("SR-1", "Brazilian authorities listed Lena Varga, 40, as a politically exposed person in March 2015."),
        sr("SR-2", "A report names Brazil resident Lena Varga, 40, as a politically exposed person (May 2015)."),
    )
    f = gather_hit_facts(ds, make_hit(categories="PEP", nationality="Brazil"))
    assert [r.result_id for r in f.relevant_search_results] == ["SR-1", "SR-2"]


def test_qualified_country_name_is_not_the_hit_country():
    ds = make_ds(sr("SR-1", "Lena Varga, 40, a politically exposed person in South Sudan, was listed in May 2015."))
    f = gather_hit_facts(ds, make_hit(categories="PEP", countries="Sudan"))
    assert f.relevant_search_results == ()


def test_search_results_not_used_for_dob_when_profile_gives_one():
    ds = make_ds(sr("SR-1", "Lena Varga, 33, a politically exposed person in Brazil, was listed in May 2019."))
    hit = make_hit(categories="PEP", countries="Brazil", profile_notes="Lena Varga was born in 1950.")
    f = gather_hit_facts(ds, hit)
    assert [r.result_id for r in f.relevant_search_results] == ["SR-1"]  # still reported
    assert years(f) == [1950] and not f.dob_from_search_only


def test_company_dob_only_from_dobs_field():
    ds = make_ds(sr("SR-1", "Varga Lena, 33, a politically exposed person in Brazil, was listed in May 2019."))
    hit = make_hit(entity_type="Company", categories="PEP", countries="Brazil",
                   profile_notes="Lena Varga was born in 1950.")
    f = gather_hit_facts(ds, hit)
    assert f.is_company and f.dob_candidates == ()


def test_age_wordings_in_snippets():
    for text in (
        "In February 2010, at the age of 33, Lena Varga was profiled.",
        "Lena Varga, a nurse in Brazil, received a community award at 33 in February 2010.",
        "A newspaper interviewed 33, Lena Varga, a taxi driver, in February 2010.",
        "In February 2010, Lena Varga (33) was named in a report.",
        "Lena Varga, then 33, was named in a February 2010 report.",
    ):
        assert dobs_from_text(text) == [((1976, 1977), None, True)], text


def test_relevant_snippet_with_unusual_age_wording_uses_lone_number():
    ds = make_ds(sr("SR-1", "Brazil's Lena Varga, a politically exposed person, made news in May 2019 aged-out 33."))
    f = gather_hit_facts(ds, make_hit(categories="PEP", countries="Brazil"))
    assert years(f) == [1985, 1986]


# ------------------------------------------------------------------- dated reports


def test_only_exact_dated_report_lines_count_earliest_first():
    notes = "\n".join([
        "According to the paper.example; September 10, 2025: Lena Varga was charged.",
        "According to the FIA - Govt; June 2014: no day, does not count.",
        "According to the Federal Bureau of Prisons, information as of December 13, 2017:",
        "According to SAM; as of May 15, 2019: does not count.",
        "Minister (December 10, 2011 - May 02, 2018);",
        "Segun la fuente; Julio 22, 2019: no.",
        "  According to news.example; January 08, 2020: earlier report.",
    ])
    reports = dated_reports(notes)
    assert [str(r.date) for r in reports] == ["2020-01-08", "2025-09-10"]
    assert reports[0].source == "news.example"
    f = gather_hit_facts(make_ds(), make_hit(profile_notes=notes))
    assert f.earliest_dated_report == reports[0]


# ------------------------------------------------------------------ deceased / type


def test_deceased_from_field_or_clear_text_only():
    f = gather_hit_facts(make_ds(), make_hit(life_status="Deceased", deceased_date="2022-03"))
    assert f.is_deceased and f.deceased_on == PartialDate(2022, 3) and f.deceased_source == "deceased_date"
    hit = make_hit(life_status="Deceased", position_or_allegation="Brother of X, Member of Congress. Deceased March 2022.")
    f = gather_hit_facts(make_ds(), hit)
    assert f.deceased_on == PartialDate(2022, 3) and f.deceased_source == "position_or_allegation"
    f = gather_hit_facts(make_ds(), make_hit(life_status="Deceased", position_or_allegation="Mother of X. Deceased."))
    assert f.is_deceased and f.deceased_on is None
    f = gather_hit_facts(make_ds(), make_hit(profile_notes="In Connecticut, 1038 people died of overdoses in 2017."))
    assert not f.is_deceased and f.deceased_on is None


def test_countries_tied_to_hit():
    hit = make_hit(
        countries="International; Peru",
        nationality="Belgrade, Serbia",
        addresses="Zurich, Switzerland [Business]; Europe",
        place_of_birth="Pont-l'Abbe, Brittany, France",
    )
    f = gather_hit_facts(make_ds(), hit)
    assert f.countries_raw == ("Peru", "Serbia", "Switzerland", "France")
    assert set(f.countries) == {"Peru", "Serbia", "Switzerland", "France"}
    assert list(f.countries) == sorted(f.countries)


def test_deterministic():
    ds = make_ds(sr("SR-2", "x"), sr("SR-1", "y"))
    a = gather_hit_facts(ds, make_hit(profile_notes="Lena Varga, aged 45, in March 2019."))
    b = gather_hit_facts(ds, make_hit(profile_notes="Lena Varga, aged 45, in March 2019."))
    assert a == b
    assert [r.result_id for r in a.search_results] == ["SR-1", "SR-2"]
