"""Gather a hit's facts wherever they are (SOP principle 5).

* names + aliases
* dates of birth, each with its source and the record that gave it:
    1. the `dobs` field;
    2. a sentence in the FIRST paragraph of profile_notes (text before the first
       blank line): an explicit birth date ("born on 17 July 1939", "born in 1952")
       or an age at a date ("aged 45 in March 2019" -> 1973 or 1974);
    3. only when neither gives one: search results that are about THIS hit (see
       below). Those are weak (`DOB_FROM_SEARCH_RESULT`) and cite the SR id.
  Companies: only the `dobs` field is an incorporation date.
* dated reports: ONLY lines of the exact form
  "According to <source>; <Month> <D>, <YYYY>: ..." (earliest first).
* deceased date: `deceased_date`, else a clearly stated "Deceased March 2022" /
  "died on <date>" in the profile text (conservative).
* countries tied to the hit: countries, nationality, citizenship, the last part of
  each address and of place_of_birth. "International" / "Europe" are not countries.
* search results about the hit: the snippet carries the hit's listing phrase (from
  its primary category, else its first category) AND names a country tied to the
  hit. Most results are namesakes.

Wording is varied on purpose in the unseen set, except the dated-report form and the
listing phrases, so the extractors here are anchored on robust cues (an age number
and a date in the same sentence) rather than on dev sentence templates.

Normalisers owned by other issues (normalize.dates / normalize.countries) are used
through their public names, with small local fallbacks while they are stubs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import Hit, SearchResult
from .loaders import Dataset
from .normalize.dates import PartialDate
from .normalize.names import name_parts, strip_accents

WEAK_SEARCH_RESULT = "DOB_FROM_SEARCH_RESULT"

# Listing phrase per category, used to decide if a search result is about the hit.
LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
DEFAULT_LISTING_PHRASE = "named on a watchlist"
_NOT_COUNTRIES = frozenset({"international", "europe", "global", "worldwide", "unknown", "n/a", "none"})


# --------------------------------------------------------------------- data shapes


@dataclass(frozen=True)
class DobCandidate:
    """One birth date the hit gives.

    years      possible birth years (1 normally; 2 for an age at a date)
    date       exact partial date when one is written (dobs field, "born on ...")
    raw        the text it came from (the dobs value or the sentence)
    source     "dobs" | "profile_notes" | "search_result"
    evidence   record id that gave it: the hit id, or the SR id for search results
    from_age   True when derived from an age at a date
    weak       True for search-result DOBs (routes decisions to BORDERLINE)
    """

    years: tuple[int, ...]
    date: PartialDate | None
    raw: str
    source: str
    evidence: str
    from_age: bool = False
    weak: bool = False

    @property
    def source_tag(self) -> str:
        """"dobs" | "profile_notes" | "SR-xxxx" (the dob_sources convention)."""
        return self.evidence if self.source == "search_result" else self.source

    @property
    def weak_why(self) -> str | None:
        return WEAK_SEARCH_RESULT if self.weak else None


@dataclass(frozen=True)
class DatedReport:
    date: PartialDate
    source: str  # who reported it ("the leparisien.fr")
    line: str  # the full line as written


@dataclass(frozen=True)
class HitFacts:
    hit_id: str
    names: tuple[str, ...]  # primary name first, then aliases
    dobs_raw: tuple[str, ...]  # parseable values; age candidates give one entry per year
    dob_sources: tuple[str, ...]  # parallel to dobs_raw: "dobs" | "profile_notes" | "SR-xxxx"
    countries_raw: tuple[str, ...]  # last part of each country-bearing value, not-countries dropped
    deceased_date: str | None  # as written (field, else clearly stated in the profile)
    listing_phrase: str
    search_results: tuple[SearchResult, ...]  # all results filed under the name (namesakes too)
    # ---- added for SOP P5 (issue #7)
    entity_type: str | None = None  # as written
    is_company: bool = False
    dob_candidates: tuple[DobCandidate, ...] = ()
    dob_from_search_only: bool = False  # every DOB came from search results -> weak
    notes_dob_unparsed: tuple[str, ...] = ()  # first-paragraph birth/age sentences we could not read
    dated_reports: tuple[DatedReport, ...] = ()  # earliest first
    earliest_dated_report: DatedReport | None = None
    deceased_on: PartialDate | None = None
    deceased_source: str | None = None  # "deceased_date" | field name the text came from
    is_deceased: bool = False  # life_status says so or a death date is known
    countries: tuple[str, ...] = ()  # canonical names, sorted
    relevant_search_results: tuple[SearchResult, ...] = ()  # about THIS hit (phrase + country)

    @property
    def dob_weak(self) -> bool:
        return self.dob_from_search_only

    @property
    def dob_evidence(self) -> tuple[str, ...]:
        """Record ids behind the DOB candidates (hit id and/or SR ids), sorted."""
        return tuple(sorted({c.evidence for c in self.dob_candidates}))


# ------------------------------------------------------------- normaliser fallbacks


def _parse_date(raw: str | None) -> PartialDate | None:
    try:
        from .normalize.dates import parse_date

        value = parse_date(raw)
    except NotImplementedError:
        value = None
    if value is None:
        value = _parse_date_local(raw)
    if value is None:
        return None
    return _valid(value.year, value.month, value.day)


_MONTHS = {
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


def _parse_date_local(raw: str | None) -> PartialDate | None:
    if not raw:
        return None
    text = re.sub(r"\s+", " ", str(raw).replace(",", " ").replace(".", " ")).strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", text):
        return _valid(int(m[1]), int(m[2]), int(m[3]) if m[3] else None)
    if m := re.fullmatch(r"(\d{4})", text):
        return PartialDate(int(m[1]))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):  # day-first
        return _valid(int(m[3]), int(m[2]), int(m[1]))
    if (m := re.fullmatch(r"([A-Za-z]+) (\d{1,2}) (\d{4})", text)) and m[1].lower() in _MONTHS:
        return _valid(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    if (m := re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", text)) and m[2].lower() in _MONTHS:
        return _valid(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    if (m := re.fullmatch(r"([A-Za-z]+) (\d{4})", text)) and m[1].lower() in _MONTHS:
        return _valid(int(m[2]), _MONTHS[m[1].lower()], None)
    return None


def _valid(year: int, month: int | None, day: int | None) -> PartialDate | None:
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    return PartialDate(year, month, day)


def _normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if _fold(text) in _NOT_COUNTRIES:
        return None
    try:
        from .normalize.countries import normalize_country

        value = normalize_country(text)
    except NotImplementedError:
        value = text.title()
    if not value or _fold(value) in _NOT_COUNTRIES:
        return None
    return value


def _countries_in_text(text: str) -> set[str]:
    """Canonical countries the shared normaliser finds in free text (empty if absent)."""
    try:
        from .normalize import countries as _c

        finder = getattr(_c, "countries_in_text", None) or getattr(_c, "find_countries", None)
        if finder is None:
            return set()
        found = finder(text)
    except NotImplementedError:
        return set()
    out: set[str] = set()
    for item in found or ():
        value = _normalize_country(item if isinstance(item, str) else str(item))
        if value:
            out.add(value)
    return out


# ---------------------------------------------------------------- country helpers


def _fold(text: str) -> str:
    """Lowercase, no accents, apostrophes unified, possessive 's dropped, other punctuation -> spaces."""
    text = strip_accents(text).lower().replace("’", "'").replace("`", "'")
    text = re.sub(r"'s\b", "", text)
    return " ".join(re.sub(r"[^0-9a-z']+", " ", text).split())


def _last_part(value: str) -> str:
    """'Ribeirao Pires, Sao Paulo, Brazil [Residency]' -> 'Brazil'."""
    text = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", value)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return parts[-1] if parts else ""


# Irregular demonyms / short forms. Regular ones (-n, -ian, -i, -ese ...) are guessed.
_IRREGULAR = {
    "united kingdom": ("british", "britain", "great britain", "uk", "england", "scotland", "wales"),
    "united states": ("american", "usa", "united states of america", "u s a"),
    "netherlands": ("dutch", "holland", "the netherlands"),
    "france": ("french",), "spain": ("spanish",), "germany": ("german",), "denmark": ("danish",),
    "sweden": ("swedish",), "finland": ("finnish",), "poland": ("polish",), "ireland": ("irish",),
    "turkey": ("turkish", "turkiye"), "greece": ("greek",), "switzerland": ("swiss",),
    "norway": ("norwegian",), "iceland": ("icelandic",), "philippines": ("filipino", "philippine"),
    "thailand": ("thai",), "china": ("chinese",), "portugal": ("portuguese",),
    "peru": ("peruvian",), "argentina": ("argentine", "argentinian"), "afghanistan": ("afghan",),
    "panama": ("panamanian",), "cote d'ivoire": ("ivorian", "ivory coast"),
    "ivory coast": ("ivorian", "cote d'ivoire"), "czech republic": ("czech", "czechia"),
    "united arab emirates": ("emirati", "uae"), "russia": ("russian federation",),
    "saudi arabia": ("saudi",), "dominican republic": ("dominican",), "kosovo": ("kosovar",),
    "trinidad and tobago": ("trinidadian",), "bosnia and herzegovina": ("bosnian",),
    "palestina": ("palestinian", "palestine"), "palestine": ("palestinian", "palestina"),
    "new zealand": ("new zealander",), "slovakia": ("slovak",), "slovenia": ("slovene",),
    "montenegro": ("montenegrin",), "azerbaijan": ("azeri",), "luxembourg": ("luxembourgish",),
    "cyprus": ("cypriot",), "madagascar": ("malagasy",), "somalia": ("somali",),
}
# A guessed demonym that actually names a different country.
_BAD_GUESSES = frozenset({("niger", "nigerian"), ("dominica", "dominican")})
# A country name preceded by one of these is a different place (South Sudan, Northern Ireland).
_QUALIFIERS = frozenset(
    {"south", "north", "northern", "southern", "east", "west", "equatorial", "new", "papua",
     "american", "british", "central", "democratic", "french", "us"}
)


def _country_terms(country: str) -> set[str]:
    base = _fold(country)
    if not base:
        return set()
    terms = {base, *(_fold(t) for t in _IRREGULAR.get(base, ()))}
    if len(base) >= 4:
        stem = base[:-1] if base[-1] in "aeiouy" else base
        for guess in (base + "n", base + "ian", base + "i", base + "ese", base + "er",
                      stem + "ian", stem + "an", stem + "ese"):
            if (base, guess) not in _BAD_GUESSES:
                terms.add(guess)
    return {t for t in terms if len(t) >= 3 or t in {"uk"}}


def _text_mentions(text_folded: str, term: str) -> bool:
    padded = f" {text_folded} "
    needle = f" {term} "
    start = padded.find(needle)
    while start != -1:
        before = padded[:start].split()
        if not before or before[-1] not in _QUALIFIERS or term.split()[0] in _QUALIFIERS:
            return True
        start = padded.find(needle, start + 1)
    return False


def _countries_tied(text: str, hit_countries_raw: tuple[str, ...], hit_countries: tuple[str, ...]) -> list[str]:
    """Hit countries (canonical) that the text names, in any phrasing."""
    folded = _fold(text)
    found = set(hit_countries) & _countries_in_text(text)
    for raw in hit_countries_raw:
        canon = _normalize_country(raw) or raw
        terms = _country_terms(raw) | _country_terms(canon)
        if any(_text_mentions(folded, t) for t in sorted(terms)):
            found.add(canon)
    return sorted(found)


# ---------------------------------------------------------------- text extraction

_M = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_DATE_EXPR = re.compile(
    rf"\b{_M}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"  # March 5, 2019
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_M},?\s+\d{{4}}\b"  # 5 March 2019
    r"|\b\d{4}-\d{2}(?:-\d{2})?\b"  # 2019-03-05
    r"|\b\d{1,2}/\d{1,2}/\d{4}\b"  # 05/03/2019 (day-first)
    rf"|\b{_M},?\s+\d{{4}}\b"  # March 2019
)
_BARE_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b(?!s|-\d)")
_REF_YEAR = re.compile(
    r"\b(?:in|during|by|of|throughout|from|since|around|circa|until|on|a|an)\s+(?:the\s+year\s+)?"
    r"(1[89]\d\d|20\d\d)\b(?!s|-\d)",
    re.I,
)
_BIRTH_WORD = re.compile(
    r"\b(?:born|birth|birthdate|birthday|DOB|D\.O\.B\.?|date\s+of\s+birth|year\s+of\s+birth)\b", re.I
)
_APPROX = re.compile(r"\b(?:circa|ca\.|c\.|around|about|approximately|approx\.?|some\s?time\s+in)\s*$", re.I)
_L = r"[^\W\d_]"  # any letter, accents included
_AGE_PATTERNS = (
    re.compile(r"\baged\s+(\d{1,3})\b", re.I),
    re.compile(r"\bage\s+of\s+(\d{1,3})\b", re.I),
    re.compile(r"\bage\s*:?\s*(\d{1,3})\b(?!\s*(?:-|to|and)\s*\d)", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:-|\s)\s*years?\s*(?:-|\s)\s*old\b", re.I),
    re.compile(r"\b(\d{1,3})\s+years?\s+of\s+age\b", re.I),
    # "... was 38 when ..." (any subject: the "when" makes it an age)
    re.compile(r"\b(?:was|turned|reached)\s+(\d{1,3})\s+(?:when|at\s+the\s+time|upon|while)\b", re.I),
    # "<Name>/he/who was 38 ..." (a capitalised subject keeps "the fine was 50" out)
    re.compile(
        rf"(?:\b[A-Z](?:{_L}|['\-])+,?|\b[Hh]e|\b[Ss]he|\b[Ww]ho)\s+(?:was|is|turned|reached)\s+(\d{{1,3}})\b"
        r"(?=\s+(?:when|and|at|in|during|as|upon|on|by|while)\b|\s*[,.;]|\s*$)"
    ),
    # apposition after a name: "Giampiero Fiorani, 35, a ...", "TAYLOR David W, 44, ..."
    re.compile(
        rf"(?:\b[A-Z](?:{_L}|['\.\-])*|/{_L}+),\s(\d{{1,3}})(?=\s*[,.;]|\s*$|\s+(?:who|was|is|had|and|when)\b)"
    ),
    # "Andrew White (38)"
    re.compile(rf"\b[A-Z]{_L}+\s+\((\d{{1,3}})\)"),
    # "... received a community award at 30." / "Charlotte Wilson, at 24, ..." / "who at 32 was"
    re.compile(
        r"\bat\s+(\d{1,3})(?=\s*[,.;]|\s*$|\s+(?:years?|was|is|when)\b|\s+(?:in|during)\s+(?:[A-Z]|\d{4}))",
        re.I,
    ),
    # "Zainab Adewale, then 19." / "who was then 62" / "celebrated being 43 and ..."
    re.compile(r"\b(?:then|being)\s+(\d{1,3})(?=\s*[,.;]|\s*$|\s+(?:and|when|years?)\b)", re.I),
    re.compile(r"\ba\s+(\d{1,3})(?=,)", re.I),
)
_AGE_HINT = re.compile(r"\b(?:aged?|years?[\s-]+old|year-old|years?\s+of\s+age)\b", re.I)
# Sentences about somebody else (relatives, co-accused) are not the hit's DOB.
_OTHER_PERSON = re.compile(
    r"\b(?:son|daughter|wife|husband|brother|sister|father|mother|child|children|spouse|"
    r"nephew|niece|cousin|uncle|aunt|grandson|granddaughter|grandfather|grandmother|"
    r"co-accused|co-defendant|accomplice|associate|victim|girlfriend|boyfriend|widow)s?\b",
    re.I,
)
_ABBREV = re.compile(
    r"(?:\b[A-Z]|Mr|Mrs|Ms|Dr|St|Govt|No|Jr|Sr|Co|Inc|Ltd|Gen|Col|Lt|Rev|Prof|U\.S|U\.K|vs|etc)\.$"
)


def first_paragraph(text: str | None) -> str:
    """Text before the first blank line."""
    if not text:
        return ""
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line.strip())
    return "\n".join(lines)


def sentences(paragraph: str) -> list[str]:
    out: list[str] = []
    for line in paragraph.split("\n"):
        pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'(])", line.strip())
        buf = ""
        for piece in pieces:
            buf = f"{buf} {piece}".strip() if buf else piece
            if not _ABBREV.search(buf):
                out.append(buf)
                buf = ""
        if buf:
            out.append(buf)
    return [s for s in out if s]


def _clean_date_text(text: str) -> str:
    text = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\bof\s+", "", text)
    return re.sub(r"\s+", " ", text.replace(",", " ").replace(".", " ")).strip()


def _parse_text_date(text: str) -> PartialDate | None:
    """A date found inside a hit profile, written as the provider wrote it.

    Slash dates there can be month-first ("DOB: 6/20/1955"): take the order only
    when one number is above 12; otherwise keep just the (certain) year.
    """
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", text)
    if m:
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        if a > 12 and b <= 12:
            return _valid(year, b, a)
        if b > 12 and a <= 12:
            return _valid(year, a, b)
        if a <= 12 and b <= 12:
            return PartialDate(year)
        return None
    return _parse_date(_clean_date_text(text))


_BIRTH_GAP = re.compile(
    r"\s*(?:[:\-]\s*)?(?:(?:is|was|recorded\s+as|listed\s+as|given\s+as)\s+)?"
    r"(?:(?:on|in|around|circa|c\.|ca\.|about|approximately|approx\.?|sometime\s+in)\s+)?"
    r"(?:the\s+year\s+)?",
    re.I,
)
_BIRTH_PLACE_GAP = re.compile(
    rf"\s*(?:in|at)\s+[A-Z](?:{_L}|['\-])*(?:,?\s+[A-Z](?:{_L}|['\-])*){{0,5}},?\s+"
    r"(?:on|in)\s+(?:(?:around|circa|about|approximately)\s+)?"
)


def _birth_from_sentence(sentence: str) -> tuple[tuple[int, ...], PartialDate | None] | None:
    m = _BIRTH_WORD.search(sentence)
    if not m:
        return None
    if _OTHER_PERSON.search(sentence[: m.start()]):
        return None
    tail = sentence[m.end(): m.end() + 70]
    dm = _DATE_EXPR.search(tail)
    ym = _BARE_YEAR.search(tail)
    first = dm if dm and (not ym or dm.start() <= ym.start()) else ym
    # the date must follow the birth word directly ("born on ...", "DOB: ...") or after a
    # place ("born in Lagos, Nigeria, in 1960"); "Born in Lagos, X was 45 in 2019" is not a DOB
    if first is None or not (_BIRTH_GAP.fullmatch(tail[: first.start()])
                             or _BIRTH_PLACE_GAP.fullmatch(tail[: first.start()])):
        return None
    if first is dm:
        date = _parse_text_date(dm.group(0))
        if date is None:
            return None
        years: tuple[int, ...] = (date.year,)
        end = dm.end()
    elif ym:
        date = PartialDate(int(ym.group(1)))
        years = (date.year,)
        end = ym.end()
        if _APPROX.search(tail[: ym.start()]):
            years = (date.year - 1, date.year, date.year + 1)
            date = None
    else:
        return None
    alt = re.match(r"\s*(?:or|/)\s*(1[89]\d\d|20\d\d)\b", tail[end:])
    if alt:
        years = tuple(sorted({*years, int(alt.group(1))}))
        if len(years) > 1:
            date = None
    return years, date


# age before a name: "an interview with 26, Maria Miller, a taxi driver"
_AGE_BEFORE_NAME = re.compile(rf"(?<![\d,.])(\d{{1,3}}),\s+(?=[A-Z](?:{_L}|['\-])*\s+[A-Z])")


def _ages(sentence: str) -> set[int]:
    found: set[int] = set()
    for rx in _AGE_PATTERNS:
        for m in rx.finditer(sentence):
            age = int(m.group(1))
            if 0 < age <= 120:
                found.add(age)
    for m in _AGE_BEFORE_NAME.finditer(sentence):
        before = sentence[: m.start()].split()
        if before and before[-1].strip(",.").lower() in _MONTHS:  # "On March 5, John ..."
            continue
        age = int(m.group(1))
        if 0 < age <= 120:
            found.add(age)
    return found


def _reference_years(sentence: str) -> set[int]:
    years: set[int] = set()
    for m in _DATE_EXPR.finditer(sentence):
        date = _parse_text_date(m.group(0))
        if date is not None:
            years.add(date.year)
    if not years:
        years = {int(m.group(1)) for m in _REF_YEAR.finditer(sentence)}
    return years


def _age_from_sentence(sentence: str) -> tuple[int, ...] | None:
    """An age plus one reference date in the same sentence -> (year-age-1, year-age)."""
    if _OTHER_PERSON.search(sentence):
        return None
    ages = _ages(sentence)
    refs = _reference_years(sentence)
    if len(ages) != 1 or len(refs) != 1:
        return None
    age, ref = next(iter(ages)), next(iter(refs))
    born = ref - age
    if born < 1850:
        return None
    return (born - 1, born)


def dobs_from_text(text: str) -> list[tuple[tuple[int, ...], PartialDate | None, bool]]:
    """(years, exact date or None, from_age) for one sentence or snippet."""
    birth = _birth_from_sentence(text)
    if birth is not None:
        return [(birth[0], birth[1], False)]
    years = _age_from_sentence(text)
    if years is not None:
        return [(years, None, True)]
    return []


def _mentions_birth_or_age(sentence: str) -> bool:
    """Birth wording next to a year, or age wording: a DOB we should have been able to read."""
    if _BIRTH_WORD.search(sentence) and _BARE_YEAR.search(sentence):
        return True
    return bool(_AGE_HINT.search(sentence))


# ------------------------------------------------------------------ dated reports

_DATED_REPORT = re.compile(
    r"^According to (?P<src>[^;\n]+); "
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r" (?P<day>\d{1,2}), (?P<year>\d{4}):"
)
_MONTH_NUM = {
    name: i
    for i, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"),
        start=1,
    )
}


def dated_reports(profile_notes: str | None) -> tuple[DatedReport, ...]:
    """Lines of the exact form 'According to <src>; <Month> <D>, <YYYY>: ...', earliest first."""
    out: list[DatedReport] = []
    for line in (profile_notes or "").splitlines():
        line = line.strip()
        m = _DATED_REPORT.match(line)
        if not m:
            continue
        day = int(m["day"])
        if not 1 <= day <= 31:
            continue
        date = PartialDate(int(m["year"]), _MONTH_NUM[m["month"]], day)
        out.append(DatedReport(date=date, source=m["src"].strip(), line=line))
    return tuple(sorted(out, key=lambda r: (r.date, r.line)))


# ------------------------------------------------------------------------ deceased

_DEATH = re.compile(
    rf"(?:^|[.;]\s*|\b(?:he|she)\s+)(?:deceased|died|passed\s+away|date\s+of\s+death)\s*[:,]?\s*"
    rf"(?:on\s+|in\s+)?(?P<date>{_DATE_EXPR.pattern}|(?:1[89]\d\d|20\d\d)\b(?!s))",
    re.I,
)


def _deceased(hit: Hit) -> tuple[str | None, PartialDate | None, str | None]:
    if hit.deceased_date:
        return hit.deceased_date, _parse_date(hit.deceased_date), "deceased_date"
    for field_name in ("position_or_allegation", "details", "profile_notes"):
        value = getattr(hit, field_name) or ""
        if field_name == "profile_notes":
            value = first_paragraph(value)
        for sentence in sentences(value):
            m = _DEATH.search(sentence)
            if m and not _OTHER_PERSON.search(sentence[: m.start("date")]):
                raw = m.group("date")
                date = _parse_text_date(raw)
                if date is not None:
                    return raw, date, field_name
    return None, None, None


# ---------------------------------------------------------------- listing / search


def search_query_key(name: str | None) -> str:
    """Search results are filed under the name's lowercase words in alphabetical order."""
    return " ".join(sorted(name_parts(name)))


def _category_key(category: str) -> str | None:
    key = " ".join(category.strip().lower().replace("-", " ").split())
    if key.startswith("sanction"):
        return "sanctions"
    if key == "pep" or key.startswith("pep ") or "politically exposed" in key:
        return "pep"
    if "enforcement" in key:
        return "enforcement"
    if "adverse media" in key:
        return "adverse media"
    if key == "soe" or "state owned" in key:
        return "soe"
    return None


def listing_phrase(hit: Hit) -> str:
    category = hit.primary_category or (hit.categories[0] if hit.categories else "") or ""
    key = _category_key(category)
    return LISTING_PHRASES.get(key, DEFAULT_LISTING_PHRASE) if key else DEFAULT_LISTING_PHRASE


def _phrase_core(phrase: str) -> str:
    return re.sub(r"^(?:a|an|the)\s+", "", phrase.strip().lower())


def has_listing_phrase(snippet: str, phrase: str) -> bool:
    core = re.escape(_phrase_core(phrase)).replace(r"\ ", r"\s+").replace(r"\-", r"[\s-]?")
    return re.search(rf"\b{core}\b", snippet or "", re.I) is not None


def is_about_hit(
    result: SearchResult, phrase: str, countries_raw: tuple[str, ...], countries: tuple[str, ...]
) -> bool:
    text = f"{result.snippet or ''}"
    if not has_listing_phrase(text, phrase):
        return False
    return bool(_countries_tied(text, countries_raw, countries))


# ------------------------------------------------------------------------- gather


def _countries_of(hit: Hit) -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw: list[str] = []
    for value in (*hit.countries, *hit.nationality, *hit.citizenship, *hit.addresses,
                  *((hit.place_of_birth,) if hit.place_of_birth else ())):
        last = _last_part(value)
        if last and _fold(last) not in _NOT_COUNTRIES:
            raw.append(last)
    raw = list(dict.fromkeys(raw))
    canon = sorted({c for c in (_normalize_country(r) for r in raw) if c})
    return tuple(raw), tuple(canon)


def _is_company(entity_type: str | None) -> bool:
    return (entity_type or "").strip().lower() in {
        "company", "organisation", "organization", "entity", "business", "corporate"
    }


def _notes_dobs(hit: Hit) -> tuple[list[DobCandidate], list[str]]:
    para = first_paragraph(hit.profile_notes)
    if not para:
        return [], []
    is_report = para.startswith("According to")
    hit_parts = {p for n in (hit.name, *hit.aliases) for p in name_parts(n)}
    found: list[DobCandidate] = []
    unparsed: list[str] = []
    for sentence in sentences(para):
        if not _mentions_birth_or_age(sentence) and not _ages(sentence):
            continue
        # inside a news report other people are named too: insist on the hit's name
        if is_report and not (set(name_parts(sentence)) & hit_parts):
            if _mentions_birth_or_age(sentence):
                unparsed.append(sentence)
            continue
        got = dobs_from_text(sentence)
        if not got:
            if _mentions_birth_or_age(sentence):
                unparsed.append(sentence)
            continue
        for years, date, from_age in got:
            found.append(DobCandidate(years, date, sentence, "profile_notes", hit.hit_id, from_age))
    return found, unparsed


def _lone_number_age(snippet: str) -> tuple[int, ...] | None:
    """Search snippets are short ("<name>, <age>, <role> in <country>, <Month YYYY>").
    When no age wording is recognised, a single stand-alone number next to a single
    date is the age."""
    if _OTHER_PERSON.search(snippet):
        return None
    refs = _reference_years(snippet)
    rest = _DATE_EXPR.sub(" ", snippet)
    rest = _BARE_YEAR.sub(" ", rest)
    numbers = {int(n) for n in re.findall(r"(?<![\d/:.\-])\b(\d{1,3})\b(?![\d/:\-]|\.\d|\s*%)", rest)}
    if len(numbers) != 1 or len(refs) != 1:
        return None
    age, ref = next(iter(numbers)), next(iter(refs))
    if not 10 <= age <= 110:
        return None
    return (ref - age - 1, ref - age)


def _search_dobs(results: tuple[SearchResult, ...]) -> list[DobCandidate]:
    out: list[DobCandidate] = []
    for result in results:
        got = dobs_from_text(result.snippet or "")
        if not got and (years := _lone_number_age(result.snippet or "")):
            got = [(years, None, True)]
        for years, date, from_age in got:
            out.append(
                DobCandidate(years, date, result.snippet or "", "search_result", result.result_id,
                             from_age, weak=True)
            )
    return out


def _flatten(cands: list[DobCandidate]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw: list[str] = []
    src: list[str] = []
    for c in cands:
        if c.source == "dobs":
            raw.append(c.raw)
            src.append(c.source_tag)
            continue
        values = [str(c.date)] if c.date is not None else [str(y) for y in c.years]
        for v in values:
            raw.append(v)
            src.append(c.source_tag)
    return tuple(raw), tuple(src)


def gather_hit_facts(ds: Dataset, hit: Hit) -> HitFacts:
    names = tuple(n for n in ((hit.name,) + tuple(hit.aliases)) if n)
    countries_raw, countries = _countries_of(hit)
    phrase = listing_phrase(hit)
    company = _is_company(hit.entity_type)

    results = tuple(sorted(ds.search_by_query.get(search_query_key(hit.name), []),
                           key=lambda r: r.result_id))
    relevant = tuple(r for r in results if is_about_hit(r, phrase, countries_raw, countries))

    candidates: list[DobCandidate] = []
    for value in hit.dobs:
        date = _parse_date(value)
        years = (date.year,) if date else ()
        candidates.append(DobCandidate(years, date, value, "dobs", hit.hit_id))
    unparsed: list[str] = []
    if not company:
        notes, unparsed = _notes_dobs(hit)
        candidates.extend(notes)
        if not candidates:
            candidates.extend(_search_dobs(relevant))
    if hit.dobs:
        unparsed = []  # the dobs field is the DOB source; stray notes wording does not matter
    dobs_raw, dob_sources = _flatten(candidates)

    reports = dated_reports(hit.profile_notes)
    dec_raw, dec_date, dec_src = _deceased(hit)
    life = (hit.life_status or "").strip().lower()
    return HitFacts(
        hit_id=hit.hit_id,
        names=names,
        dobs_raw=dobs_raw,
        dob_sources=dob_sources,
        countries_raw=countries_raw,
        deceased_date=dec_raw,
        listing_phrase=phrase,
        search_results=results,
        entity_type=hit.entity_type,
        is_company=company,
        dob_candidates=tuple(candidates),
        dob_from_search_only=bool(candidates) and all(c.weak for c in candidates),
        notes_dob_unparsed=tuple(unparsed),
        dated_reports=reports,
        earliest_dated_report=reports[0] if reports else None,
        deceased_on=dec_date,
        deceased_source=dec_src,
        is_deceased=life in {"deceased", "dead"} or dec_date is not None,
        countries=countries,
        relevant_search_results=relevant,
    )
