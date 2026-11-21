import datetime as dt

import pytest

from screening.normalize.dates import (
    PartialDate,
    all_dates_contradict,
    any_date_agrees,
    birth_dates_from_age,
    birth_years_from_age,
    dates_agree,
    dates_contradict,
    definitely_before,
    parse_date,
    parse_dates,
)

P = PartialDate


@pytest.mark.parametrize(
    "raw, expected",
    [
        # ISO and ISO-like
        ("1985-04-23", P(1985, 4, 23)),
        ("1968-02", P(1968, 2)),
        ("1985/04/23", P(1985, 4, 23)),
        ("1985.04.23", P(1985, 4, 23)),
        ("1985-04-23T00:00:00Z", P(1985, 4, 23)),
        ("1985-04-23 10:30", P(1985, 4, 23)),
        ("1985-00-00", P(1985)),
        ("1985-04-00", P(1985, 4)),
        # year only
        ("1985", P(1985)),
        ("  1985 ", P(1985)),
        # slash dates are DAY-FIRST
        ("07/02/1989", P(1989, 2, 7)),
        ("7/2/1989", P(1989, 2, 7)),
        ("23/04/1985", P(1985, 4, 23)),
        ("07-02-1989", P(1989, 2, 7)),
        ("07.02.1989", P(1989, 2, 7)),
        ("07 / 02 / 1989", P(1989, 2, 7)),
        # month-first only when day-first is impossible
        ("02/23/1989", P(1989, 2, 23)),
        ("02/1989", P(1989, 2)),
        # month names
        ("March 3, 1980", P(1980, 3, 3)),
        ("March 03 1980", P(1980, 3, 3)),
        ("Mar. 3 1980", P(1980, 3, 3)),
        ("Sept. 3, 1980", P(1980, 9, 3)),
        ("3 March 1980", P(1980, 3, 3)),
        ("03 Mar 1980", P(1980, 3, 3)),
        ("3rd March 1980", P(1980, 3, 3)),
        ("21st of March, 1980", P(1980, 3, 21)),
        ("March 22nd, 1980", P(1980, 3, 22)),
        ("March 1980", P(1980, 3)),
        ("Mar 1980", P(1980, 3)),
        ("03-Mar-1980", P(1980, 3, 3)),
        ("1980-Mar-03", P(1980, 3, 3)),
        ("Monday, March 3, 1980", P(1980, 3, 3)),
        ("MARCH 3, 1980", P(1980, 3, 3)),
        ("3 de marzo de 1980", P(1980, 3, 3)),
        ("1er février 1980", P(1980, 2, 1)),
        ("29 February 1984", P(1984, 2, 29)),
        # non-string inputs
        (1985, P(1985)),
        (dt.date(1985, 4, 23), P(1985, 4, 23)),
        (dt.datetime(1985, 4, 23, 9, 0), P(1985, 4, 23)),
        (P(1985, 4), P(1985, 4)),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "   ", "unknown", "N/A", "null", "circa 1980", "1980s", "07/02/89",
        "13/13/1980", "31/02/1980", "29 February 1983", "1985-13-01", "1985-02-30",
        "March", "March 80", "3 Marchember 1980", "born 1980", "1980 1981", "0000",
        "9999", "12/12", "1 2 March 1980", True, 3.5, [], {}, object(), "\u0000", "/" * 50,
    ],
)
def test_unparseable_returns_none(raw):
    assert parse_date(raw) is None


def test_ambiguous_slash_is_day_first():
    # both numbers <= 12: still DD/MM, never MM/DD
    assert parse_date("05/06/1990") == P(1990, 6, 5)


def test_parse_dates_lists():
    assert parse_dates("1996; 1997") == (P(1996), P(1997))
    assert parse_dates(["1997", "1996", "1996", "junk", None]) == (P(1996), P(1997))
    assert parse_dates("1979 or 1980") == (P(1979), P(1980))
    assert parse_dates("1979/1980") == (P(1979), P(1980))
    assert parse_dates("March 3, 1980") == (P(1980, 3, 3),)
    assert parse_dates("1980; 1980-03; 1980-03-03") == (P(1980), P(1980, 3), P(1980, 3, 3))
    assert parse_dates(None) == ()
    assert parse_dates("") == ()
    assert parse_dates(42.0) == ()


def test_birth_years_from_age():
    assert birth_years_from_age(45, P(2019, 3)) == (1973, 1974)
    assert birth_years_from_age("45", "March 2019") == (1973, 1974)
    assert birth_years_from_age(45, "2019") == (1973, 1974)
    assert birth_years_from_age(None, "2019") is None
    assert birth_years_from_age(45, None) is None
    assert birth_years_from_age(-1, "2019") is None
    assert birth_years_from_age(200, "2019") is None
    assert birth_years_from_age("forty", "2019") is None
    assert birth_years_from_age(True, "2019") is None
    assert birth_dates_from_age(45, "March 2019") == (P(1973), P(1974))
    assert birth_dates_from_age(None, "March 2019") == ()


@pytest.mark.parametrize(
    "customer, hit, agree, contradict",
    [
        (P(1980, 3, 3), P(1980, 3, 3), True, False),
        (P(1980, 3, 3), P(1980), True, False),  # year alone agrees with a full date
        (P(1980), P(1980, 3, 3), True, False),
        (P(1980, 3, 3), P(1980, 3), True, False),
        (P(1980, 3, 3), P(1981), False, False),  # 1 year off: neither
        (P(1980, 3, 3), P(1979, 3, 3), False, False),
        (P(1980, 3, 3), P(1982), False, True),  # >1 year off
        (P(1980), P(1978, 1, 1), False, True),
        (P(1980, 3, 3), P(1980, 4), False, True),  # same year, months differ
        (P(1980, 3, 3), P(1980, 3, 4), False, True),  # same month, days differ
        (P(1980, 3), P(1980, 4, 1), False, True),
        (P(1980), P(1980, 4, 1), True, False),
        (P(1980, 3, 3), P(1981, 4, 9), False, False),  # month only compared in same year
        (None, P(1980), False, False),  # missing fact never contradicts
        (P(1980), None, False, False),
        ("07/02/1989", "1989-02-07", True, False),  # strings are parsed
        ("07/02/1989", "1989-07-02", False, True),
        ("junk", "1989", False, False),
    ],
)
def test_agree_and_contradict(customer, hit, agree, contradict):
    assert dates_agree(customer, hit) is agree
    assert dates_contradict(customer, hit) is contradict


def test_over_hit_dates():
    cust = P(1974, 6, 1)
    assert any_date_agrees(cust, ["1960", "1974"])
    assert not all_dates_contradict(cust, ["1960", "1974"])  # one agreeing date prevents it
    assert all_dates_contradict(cust, ["1960", "1990-01-01"])
    assert not all_dates_contradict(cust, ["1960", "1975"])  # 1 year off prevents it
    assert not all_dates_contradict(cust, [])
    assert not all_dates_contradict(cust, ["junk", None])
    assert not any_date_agrees(cust, None)
    assert not all_dates_contradict(None, ["1960"])


def test_age_pair_is_one_birth_date():
    pair = birth_dates_from_age(45, "March 2019")  # 1973 or 1974
    assert any_date_agrees(P(1973, 5, 5), pair)
    assert any_date_agrees(P(1974, 5, 5), pair)
    assert not all_dates_contradict(P(1972), pair)  # 1 off the range
    assert not all_dates_contradict(P(1975), pair)
    assert all_dates_contradict(P(1971), pair)
    assert all_dates_contradict(P(1976), pair)


def test_definitely_before():
    assert definitely_before("2019-03", "2019-04-01")
    assert definitely_before("2018", "2019")
    assert not definitely_before("2019", "2019-06")
    assert not definitely_before("2019-06", "2019")
    assert not definitely_before("2019-03-31", "2019-03")
    assert definitely_before("2019-02-28", "2019-03")
    assert not definitely_before(None, "2019")
    assert not definitely_before("junk", "2019")


def test_partial_date_contract_unchanged():
    d = P(1980, 3, 3)
    assert (d.year, d.month, d.day) == (1980, 3, 3)
    assert str(d) == "1980-03-03" and str(P(1980, 3)) == "1980-03" and str(P(1980)) == "1980"
    assert P(1980, 1, 1) < P(1980, 1, 2)
    with pytest.raises(AttributeError):
        d.year = 1  # frozen
