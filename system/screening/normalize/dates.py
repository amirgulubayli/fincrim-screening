"""Partial dates from the many formats providers use.

A date can be a full date, a year+month, or a year alone. "Unknown stays
unknown": a missing part never contradicts anything.

Parsed shapes (case, accents, extra spaces and ordinals like "3rd" are ignored):

- ISO and ISO-like: ``YYYY-MM-DD``, ``YYYY-MM``, ``YYYY/MM/DD``, ``YYYY.MM.DD``,
  optionally followed by a time (``1980-02-03T00:00:00``). ``00`` parts are unknown.
- Year only: ``YYYY``.
- Numeric day-first: ``DD/MM/YYYY``, ``D/M/YYYY``, ``DD-MM-YYYY``, ``DD.MM.YYYY``.
  Slash dates in this data are DAY-FIRST (the second number is never above 12).
  Only when the day-first reading is impossible and the month-first one is valid
  (``02/23/1980``) is the month-first reading used.
- Numeric month and year: ``MM/YYYY``.
- Month names (English, French, Spanish; full or abbreviated, with or without a
  dot) in any order with a 4-digit year and an optional day: ``Month D, YYYY``,
  ``D Month YYYY``, ``Mon. D YYYY``, ``3rd of March 1980``, ``Month YYYY``,
  ``03-Mar-1980``, ``Monday, March 3, 1980``, ``3 de marzo de 1980``.

Anything else (two-digit years, ``circa``, ``1980s``, free text) returns None:
an unknown date can never clear a hit. Nothing here raises on messy input.

Comparison helpers implement SOP P6 (contradiction) and P7 (agreement):

- agree: the birth years are equal and every part both give (month, day) is equal;
  a year alone agrees with a full date.
- contradict: the years are more than 1 apart, or they are the same year, both give
  a month and the months differ, or both give the same month and the days differ.
  Years exactly 1 apart neither agree nor contradict.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

# French and Spanish month names (accents are stripped before lookup). Kept apart
# from MONTHS so the public English table does not change.
_FOREIGN_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("janvier", "enero", "ene"), ("fevrier", "febrero"), ("mars", "marzo"),
            ("avril", "abril", "abr"), ("mai", "mayo"), ("juin", "junio"),
            ("juillet", "julio"), ("aout", "agosto", "ago"),
            ("septembre", "septiembre", "setiembre"), ("octobre", "octubre"),
            ("novembre", "noviembre"), ("decembre", "diciembre", "dic"),
        ],
        start=1,
    )
    for m in names
}
_ALL_MONTHS = {**_FOREIGN_MONTHS, **MONTHS}

# Words that may surround a date without changing it.
_FILLER = frozenset(
    {
        "of", "the", "de", "del", "le", "el",
        "mon", "monday", "tue", "tues", "tuesday", "wed", "wednesday", "thu", "thur",
        "thurs", "thursday", "fri", "friday", "sat", "saturday", "sun", "sunday",
    }
)

MIN_YEAR = 1800  # fixed bounds: no clock in decisions
MAX_YEAR = 2100


@dataclass(frozen=True, order=True)
class PartialDate:
    year: int
    month: int | None = None
    day: int | None = None

    def __str__(self) -> str:
        if self.month and self.day:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.month:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"


_TIME_TAIL = r"(?:[t ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?"  # text is lowercased
_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?" + _TIME_TAIL)
_YEAR = re.compile(r"(\d{4})")
_NUM_DMY = re.compile(r"(\d{1,2})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{4})")
_NUM_MY = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{4})")
_ORDINAL = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th|er|e|o)\b")  # NFKD turns "º" into "o"
_WORDY_CHARS = re.compile(r"[a-z0-9\s,./\-']+")
_TOKEN = re.compile(r"[a-z]+|\d+")
_LIST_SPLIT = re.compile(r"[;|]")
_YEAR_LIST_SPLIT = re.compile(r",|/|&|\bor\b|\band\b")


def _make(year: int, month: int | None = None, day: int | None = None) -> PartialDate | None:
    """Validated PartialDate, or None when any given part is impossible."""
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    if month is None:
        return PartialDate(year) if day is None else None
    if not 1 <= month <= 12:
        return None
    if day is None:
        return PartialDate(year, month)
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return PartialDate(year, month, day)


def _clean(raw: str) -> str:
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace(" ", " ")
    text = text.strip().strip("\"'()[]{}").strip()
    text = text.rstrip(".,;:").strip()
    return re.sub(r"\s+", " ", text)


def _parse_iso(text: str) -> PartialDate | None:
    m = _ISO.fullmatch(text)
    if not m:
        return None
    year, month = int(m[1]), int(m[2])
    day = int(m[3]) if m[3] else None
    if month == 0:  # "1980-00-00": only the year is known
        return _make(year) if not day else None
    if day == 0:
        day = None
    return _make(year, month, day)


def _parse_numeric(text: str) -> PartialDate | None:
    if m := _NUM_DMY.fullmatch(text):
        first, second, year = int(m[1]), int(m[3]), int(m[4])
        # Day-first, always. Month-first only when day-first is impossible.
        return _make(year, second, first) or (_make(year, first, second) if second > 12 else None)
    if m := _NUM_MY.fullmatch(text):
        return _make(int(m[2]), int(m[1]))
    return None


def _parse_wordy(text: str) -> PartialDate | None:
    text = _ORDINAL.sub(r"\1", text)
    if not _WORDY_CHARS.fullmatch(text):
        return None
    month = year = day = None
    for tok in _TOKEN.findall(text):
        if tok.isdigit():
            if len(tok) == 4 and year is None:
                year = int(tok)
            elif len(tok) <= 2 and day is None:
                day = int(tok)
            else:
                return None
        elif tok in _ALL_MONTHS and month is None:
            month = _ALL_MONTHS[tok]
        elif tok not in _FILLER:
            return None
    if month is None or year is None:
        return None
    return _make(year, month, day)


def parse_date(raw: object) -> PartialDate | None:
    """One date value -> PartialDate, or None when unknown or unparseable. Never raises."""
    try:
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, PartialDate):
            return raw
        if isinstance(raw, _dt.datetime):
            return _make(raw.year, raw.month, raw.day)
        if isinstance(raw, _dt.date):
            return _make(raw.year, raw.month, raw.day)
        if isinstance(raw, int):
            return _make(raw)
        if not isinstance(raw, str):
            return None
        text = _clean(raw)
        if not text:
            return None
        if m := _YEAR.fullmatch(text):
            return _make(int(m[1]))
        return _parse_iso(text) or _parse_numeric(text) or _parse_wordy(text)
    except Exception:  # noqa: BLE001 - messy input must never break a run
        return None


def _sort_key(d: PartialDate) -> tuple[int, int, int]:
    # PartialDate's own ordering compares None with int and can raise.
    return (d.year, d.month or 0, d.day or 0)


def parse_dates(raw: object) -> tuple[PartialDate, ...]:
    """A list of dates ("1996; 1997", ["1980", "1981-03"]) -> sorted unique PartialDates.

    Unparseable entries are dropped. A piece that is not a date by itself but is a
    list of plain years ("1996, 1997", "1979 or 1980") gives each year.
    """
    try:
        if raw is None:
            return ()
        if isinstance(raw, str):
            pieces: Iterable[object] = _LIST_SPLIT.split(raw)
        elif isinstance(raw, (list, tuple)):
            pieces = raw
        else:
            pieces = [raw]
        found: set[PartialDate] = set()
        for piece in pieces:
            date = parse_date(piece)
            if date is not None:
                found.add(date)
                continue
            if isinstance(piece, str):
                parts = [p.strip() for p in _YEAR_LIST_SPLIT.split(_clean(piece))]
                years = [parse_date(p) for p in parts if p]
                if len(years) > 1 and all(y is not None and y.month is None for y in years):
                    found.update(y for y in years if y is not None)
        return tuple(sorted(found, key=_sort_key))
    except Exception:  # noqa: BLE001
        return ()


def birth_years_from_age(age: object, at_date: object) -> tuple[int, int] | None:
    """Two possible birth years for someone `age` years old at `at_date`.

    SOP P5: aged 45 in March 2019 -> (1973, 1974). Returns None when the age or
    the date is unknown or implausible.
    """
    try:
        if isinstance(age, bool):
            return None
        if isinstance(age, str):
            age = int(age.strip()) if age.strip().isdigit() else None
        if not isinstance(age, int) or not 0 <= age <= 130:
            return None
        at = parse_date(at_date)
        if at is None:
            return None
        lo, hi = at.year - age - 1, at.year - age
        if lo < MIN_YEAR:
            return None
        return (lo, hi)
    except Exception:  # noqa: BLE001
        return None


def birth_dates_from_age(age: object, at_date: object) -> tuple[PartialDate, ...]:
    """Same as birth_years_from_age, as year-only PartialDates (empty when unknown).

    Treat the pair as ONE hit birth date with two possible years: it agrees when
    either year agrees and is contradicted only when both are, which is exactly
    what any_date_agrees / all_dates_contradict do with this tuple.
    """
    years = birth_years_from_age(age, at_date)
    if years is None:
        return ()
    return tuple(PartialDate(y) for y in years)


def _coerce(value: object) -> PartialDate | None:
    return value if isinstance(value, PartialDate) else parse_date(value)


def dates_agree(customer: object, hit: object) -> bool:
    """SOP P7: same year and every part both give is equal. Unknown -> False."""
    c, h = _coerce(customer), _coerce(hit)
    if c is None or h is None or c.year != h.year:
        return False
    if c.month is not None and h.month is not None:
        if c.month != h.month:
            return False
        if c.day is not None and h.day is not None and c.day != h.day:
            return False
    return True


def dates_contradict(customer: object, hit: object) -> bool:
    """SOP P6: years >1 apart, or same year with differing months (or days). Unknown -> False."""
    c, h = _coerce(customer), _coerce(hit)
    if c is None or h is None:
        return False
    if abs(c.year - h.year) > 1:
        return True
    if c.year != h.year or c.month is None or h.month is None:
        return False
    if c.month != h.month:
        return True
    return c.day is not None and h.day is not None and c.day != h.day


def _known(values: Iterable[object]) -> list[PartialDate]:
    out = []
    for v in values or ():
        d = _coerce(v)
        if d is not None:
            out.append(d)
    return out


def any_date_agrees(customer: object, hit_dates: Iterable[object]) -> bool:
    """P7 over a hit's dates: one agreeing date is enough."""
    try:
        return any(dates_agree(customer, h) for h in _known(hit_dates))
    except Exception:  # noqa: BLE001
        return False


def all_dates_contradict(customer: object, hit_dates: Iterable[object]) -> bool:
    """P6 over a hit's dates: EVERY known date is contradicted. No dates -> False.

    A missing fact is never a contradiction, so an empty or unparseable list never
    contradicts, and one agreeing (or 1-year-off) date prevents a contradiction.
    """
    try:
        known = _known(hit_dates)
        return bool(known) and all(dates_contradict(customer, h) for h in known)
    except Exception:  # noqa: BLE001
        return False


def _bounds(d: PartialDate) -> tuple[_dt.date, _dt.date]:
    if d.month is None:
        return _dt.date(d.year, 1, 1), _dt.date(d.year, 12, 31)
    if d.day is None:
        return _dt.date(d.year, d.month, 1), _dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    day = _dt.date(d.year, d.month, d.day)
    return day, day


def definitely_before(earlier: object, later: object) -> bool:
    """True only if every reading of `earlier` falls strictly before every reading of `later`.

    For temporal checks (e.g. died before an event): 2019-03 is definitely before
    2019-04-01, but 2019 is not definitely before 2019-06. Unknown -> False.
    """
    try:
        a, b = _coerce(earlier), _coerce(later)
        if a is None or b is None:
            return False
        return _bounds(a)[1] < _bounds(b)[0]
    except Exception:  # noqa: BLE001
        return False
