"""Country normaliser (SOP P5/P6): codes, names, demonyms, territories, free text.

Hand-built values only; no data copied from the data folder.
"""

from __future__ import annotations

import pytest

from screening.normalize.countries import (
    CANONICAL_COUNTRIES,
    classify_country,
    countries_in_text,
    find_countries,
    normalize_country,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # ISO alpha-2 / alpha-3, any case, surrounding space and dots
        ("NG", "Nigeria"), ("nga", "Nigeria"), (" gb ", "United Kingdom"), ("GBR", "United Kingdom"),
        ("US", "United States"), ("USA", "United States"), ("U.S.A.", "United States"),
        ("DE", "Germany"), ("BD", "Bangladesh"), ("CI", "Côte d'Ivoire"), ("RS", "Serbia"),
        ("ZA", "South Africa"), ("XK", "Kosovo"), ("CHE", "Switzerland"),
        # English names and common alternates
        ("Nigeria", "Nigeria"), ("united kingdom", "United Kingdom"), ("UK", "United Kingdom"),
        ("Great Britain", "United Kingdom"), ("United States of America", "United States"),
        ("Cote D'Ivoire", "Côte d'Ivoire"), ("Côte d'Ivoire", "Côte d'Ivoire"), ("Ivory Coast", "Côte d'Ivoire"),
        ("Côte d’Ivoire", "Côte d'Ivoire"), ("Russian Federation", "Russia"), ("Russia", "Russia"),
        ("UAE", "United Arab Emirates"), ("Viet Nam", "Vietnam"), ("Korea, Republic of", "South Korea"),
        ("Republic of Korea", "South Korea"), ("Korea, Democratic People's Republic of", "North Korea"),
        ("DPRK", "North Korea"), ("Iran, Islamic Republic of", "Iran"), ("Syrian Arab Republic", "Syria"),
        ("Republic of Moldova", "Moldova"), ("Czechia", "Czech Republic"), ("Türkiye", "Turkey"),
        ("The Netherlands", "Netherlands"), ("Holland", "Netherlands"), ("Palestina", "Palestine"),
        ("Bosnia and Herzegovina", "Bosnia and Herzegovina"), ("Trinidad & Tobago", "Trinidad and Tobago"),
        ("St. Lucia", "Saint Lucia"), ("Swaziland", "Eswatini"), ("Burma", "Myanmar"),
        ("Macedonia", "North Macedonia"), ("Lao PDR", "Laos"), ("The Gambia", "Gambia"),
        ("Scotland", "United Kingdom"), ("Northern Ireland", "United Kingdom"),
        # demonyms / adjectives
        ("Nigerian", "Nigeria"), ("British", "United Kingdom"), ("Bangladeshi", "Bangladesh"),
        ("Irish", "Ireland"), ("Polish", "Poland"), ("Brazilian", "Brazil"), ("American", "United States"),
        ("Dutch", "Netherlands"), ("Swiss", "Switzerland"), ("Ivorian", "Côte d'Ivoire"),
        ("Emirati", "United Arab Emirates"), ("Filipino", "Philippines"), ("Ghanaian", "Ghana"),
        ("North Korean", "North Korea"), ("South Korean", "South Korea"), ("Kazakh", "Kazakhstan"),
        # bracket tags and address-style values
        ("United States [Associated]", "United States"), ("Canada [PEP]", "Canada"),
        ("Spain [Residency]", "Spain"), ("Belgrade, Serbia", "Serbia"),
        ("Faisalabad, Punjab, Pakistan", "Pakistan"), ("Street 1, Town, Czech Republic [Business]", "Czech Republic"),
        ("Hong Kong SAR", "Hong Kong"), ("Macao Special Administrative Region of China", "Macao"),
    ],
)
def test_normalize_known_values(raw, expected):
    assert normalize_country(raw) == expected


@pytest.mark.parametrize(
    "raw, territory, not_this",
    [
        ("Hong Kong", "Hong Kong", "China"),
        ("HK", "Hong Kong", "China"),
        ("Macau", "Macao", "China"),
        ("Puerto Rico", "Puerto Rico", "United States"),
        ("PR", "Puerto Rico", "United States"),
        ("Northern Mariana Islands", "Northern Mariana Islands", "United States"),
        ("Guam", "Guam", "United States"),
        ("US Virgin Islands", "United States Virgin Islands", "United States"),
        ("Gibraltar", "Gibraltar", "United Kingdom"),
        ("Jersey", "Jersey", "United Kingdom"),
        ("Guernsey", "Guernsey", "United Kingdom"),
        ("Isle of Man", "Isle of Man", "United Kingdom"),
        ("British Virgin Islands", "British Virgin Islands", "United Kingdom"),
        ("Cayman Islands", "Cayman Islands", "United Kingdom"),
        ("Bermuda", "Bermuda", "United Kingdom"),
        ("Greenland", "Greenland", "Denmark"),
        ("Curacao", "Curaçao", "Netherlands"),
        ("Reunion", "Réunion", "France"),
    ],
)
def test_territories_are_their_own_country(raw, territory, not_this):
    assert normalize_country(raw) == territory
    assert territory != not_this


@pytest.mark.parametrize(
    "raw", ["International", "Europe", "europe", "European Union", "Global", "Worldwide", "Middle East",
            "Africa", "Latin America", "Caribbean", "Unknown", "N/A", "Stateless", "Scandinavia"],
)
def test_regions_and_placeholders_are_not_countries(raw):
    assert normalize_country(raw) is None
    assert classify_country(raw) == (None, "not_country")


@pytest.mark.parametrize(
    "raw", ["Congo", "Korea", "Korean", "Congolese", "Virgin Islands", "Dominican", "Yugoslavia",
            "Soviet Union", "Netherlands Antilles", "NA"],
)
def test_ambiguous_values_are_never_guessed(raw):
    assert classify_country(raw) == (None, "ambiguous")


@pytest.mark.parametrize(
    "raw", ["Atlantis", "Wakanda", "Lagos", "ZQ", "Florida", "58 High Street, Lagos", "Panama City, Florida",
            "Atlanta, Georgia", "Nigerianish"],
)
def test_unknown_values_return_none_not_a_guess(raw):
    # A wrong country can cause an unsafe LOCATION_MISMATCH clear: unknown stays unknown.
    assert classify_country(raw) == (None, "unknown")


@pytest.mark.parametrize("raw", [None, "", "   ", "[Associated]", ",", "-"])
def test_blank_values(raw):
    assert normalize_country(raw) is None
    assert classify_country(raw)[1] in {"empty", "not_country"}


def test_table_covers_iso_3166():
    # 249 ISO 3166-1 entries plus Kosovo
    assert len(CANONICAL_COUNTRIES) == 250
    for name in CANONICAL_COUNTRIES:
        assert normalize_country(name) == name
        assert classify_country(name)[1] == "country"


def test_namibia_by_name_not_by_placeholder_code():
    assert normalize_country("Namibia") == "Namibia"
    assert normalize_country("NAM") == "Namibia"
    assert normalize_country("NA") is None


# ---------------------------------------------------------------- free text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("an official notice about a sanctioned individual within Cote D'Ivoire.", ("Côte d'Ivoire",)),
        ("Brazilian authorities opened a case.", ("Brazil",)),
        ("X, 40, a Netherlands resident, was named in adverse media in Pakistan.", ("Netherlands", "Pakistan")),
        ("identity confirmed using a Nigerian national ID", ("Nigeria",)),
        ("passport issued in Germany on 2023-02-19", ("Germany",)),
        ("living and working in South Africa from 2016 to 2020", ("South Africa",)),
        ("national ID (United Kingdom) for A B", ("United Kingdom",)),
        ("born in Belfast, Northern Ireland", ("United Kingdom",)),
        ("moved from Papua New Guinea to Guinea", ("Papua New Guinea", "Guinea")),
        ("based in the United States Virgin Islands", ("United States Virgin Islands",)),
        ("worked with U.S. and UAE regulators", ("United States", "United Arab Emirates")),
        ("lived in Côte d’Ivoire, then Ivory Coast again", ("Côte d'Ivoire",)),
        ("Trinidad & Tobago and St. Lucia", ("Trinidad and Tobago", "Saint Lucia")),
        ("Hong Kong and mainland China", ("Hong Kong", "China")),
    ],
)
def test_countries_in_text(text, expected):
    assert countries_in_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Latin American and South American partners",  # not "American" -> United States
        "an International body covering Europe and the Middle East",
        "she lives in New Jersey",  # no Jersey (it is a US state)
        "a turkey sandwich and fine china",  # ordinary words, lowercase
        "please contact us by phone",  # "us" is not US
        "trade across the Indian Ocean",
        "unrest in Congo and Korea",  # ambiguous, never guessed
        "IN IT NO DE",  # ISO codes are not read from prose
        "Jordan Andrew Chartrand was interviewed",  # a given name
        "",
    ],
)
def test_countries_in_text_rejects_non_countries(text):
    found = countries_in_text(text)
    assert "United States" not in found or "New Jersey" in text
    assert "Jersey" not in found
    assert "Turkey" not in found and "China" not in found
    assert "Congo" not in " ".join(found) and "Korea" not in " ".join(found)
    assert "Jordan" not in found and "India" not in found and "Italy" not in found
    if "New Jersey" not in text:
        assert found == ()


def test_subnational_phrases_map_to_their_country():
    assert countries_in_text("an office in New Jersey") == ("United States",)
    assert countries_in_text("Vancouver, British Columbia") == ("Canada",)
    assert countries_in_text("Sydney, New South Wales") == ("Australia",)


def test_given_name_guard_keeps_real_country_mentions():
    assert countries_in_text("In Jordan, a minister resigned") == ("Jordan",)
    assert countries_in_text("Amman, Jordan") == ("Jordan",)
    assert countries_in_text("Michael Jordan visited Spain") == ("Spain",)


def test_text_order_is_first_appearance_and_deduplicated():
    text = "Spain, then Nigeria, then Spanish courts, then Nigerian police"
    assert countries_in_text(text) == ("Spain", "Nigeria")
    assert find_countries(text) == ["Spain", "Nigeria"]


def test_deterministic():
    text = "Brazilian, Peruvian and Irish nationals in Ghana and the UK"
    first = countries_in_text(text)
    assert all(countries_in_text(text) == first for _ in range(5))
    assert first == ("Brazil", "Peru", "Ireland", "Ghana", "United Kingdom")
