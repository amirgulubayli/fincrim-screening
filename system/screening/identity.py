"""Identity graph and verified identity (SOP principles 3 and 4).

* Identity graph (P3): customer records that share a phone number AND the same full
  date of birth are one person; so are records whose APPROVED identity checks carry
  the same document number; transitively (union-find). A shared phone or address
  alone never links (family members), and neither does a partial date of birth.
* Verified identity (P4): the most recent approved identity check of a person, from
  ``kyc_checks`` or from a note written by the onboarding team. Rejected or expired
  checks are never evidence.
* Country claims (P6 location): every country tied to a person - residence and
  nationality on all records, the approved identity documents, and places the notes
  say they lived - kept RAW with the id of the record that says so.

Standard library only, deterministic: everything is iterated in id order and the
graph is built once per Dataset (cached on the Dataset object).

Normalisers owned by other issues (``normalize.dates.parse_date``,
``normalize.countries.normalize_country``) are used when they can answer; small
private fallbacks cover what they do not handle yet (day-first slash dates).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .contracts import Customer, KycCheck, Note
from .normalize.dates import MONTHS, PartialDate, parse_date
from .normalize.names import strip_accents

if TYPE_CHECKING:  # pragma: no cover
    from .loaders import Dataset

# ------------------------------------------------------------------ small helpers

_SLASH = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")


def parse_dob(raw: str | None) -> PartialDate | None:
    """parse_date, plus day-first DD/MM/YYYY until the date-parser issue lands.

    Slash dates in this bank's systems are day-first (the second number is never
    above 12). A value that cannot be read stays unknown (None).
    """
    if not raw:
        return None
    key = str(raw)
    if key in _DOB_CACHE:
        return _DOB_CACHE[key]
    try:
        parsed = parse_date(raw)
    except Exception:  # noqa: BLE001 - an unreadable date stays unknown, never a crash
        parsed = None
    result: PartialDate | None = None
    if isinstance(parsed, PartialDate):
        result = _valid(parsed)
    else:
        m = _SLASH.match(key.strip())
        if m:
            day, month, year = int(m[1]), int(m[2]), int(m[3])
            result = _valid(PartialDate(year, month, day))
    _DOB_CACHE[key] = result
    return result


_DOB_CACHE: dict[str, PartialDate | None] = {}


def _valid(d: PartialDate) -> PartialDate | None:
    if d.month is not None and not 1 <= d.month <= 12:
        return None
    if d.day is not None and not 1 <= d.day <= 31:
        return None
    return d


def is_full(d: PartialDate | None) -> bool:
    return d is not None and d.month is not None and d.day is not None


def dates_compatible(a: PartialDate | None, b: PartialDate | None) -> bool:
    """Equal in every part both give (a missing side or part never contradicts)."""
    if a is None or b is None:
        return True
    if a.year != b.year:
        return False
    if a.month is not None and b.month is not None:
        if a.month != b.month:
            return False
        if a.day is not None and b.day is not None and a.day != b.day:
            return False
    return True


def normalize_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits if len(digits) >= 6 else ""


def normalize_document(raw: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", strip_accents(raw or "").upper())


def _key(value: str | None) -> str:
    return (value or "").strip().lower()


def is_approved(check: KycCheck) -> bool:
    return _key(check.result) == "approved"


# ------------------------------------------------------------------ country lexicon
# Used only to FIND country mentions inside note text (and to give a canonical name
# next to the raw value). The official normaliser is normalize.countries.

_COUNTRY_NAMES = """
Afghanistan|Albania|Algeria|Andorra|Angola|Antigua and Barbuda|Argentina|Armenia|Aruba|
Australia|Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|
Benin|Bermuda|Bhutan|Bolivia|Bosnia and Herzegovina|Botswana|Brazil|Brunei|Bulgaria|
Burkina Faso|Burundi|Cambodia|Cameroon|Canada|Cape Verde|Cayman Islands|
Central African Republic|Chad|Chile|China|Colombia|Comoros|Congo|Costa Rica|
Cote d'Ivoire|Côte d'Ivoire|Croatia|Cuba|Curacao|Cyprus|Czech Republic|Czechia|
Democratic Republic of the Congo|Denmark|Djibouti|Dominica|Dominican Republic|Ecuador|
Egypt|El Salvador|Equatorial Guinea|Eritrea|Estonia|Eswatini|Ethiopia|Fiji|Finland|
France|Gabon|Gambia|Georgia|Germany|Ghana|Gibraltar|Greece|Greenland|Grenada|Guatemala|
Guernsey|Guinea|Guinea-Bissau|Guyana|Haiti|Honduras|Hong Kong|Hungary|Iceland|India|
Indonesia|Iran|Iraq|Ireland|Isle of Man|Israel|Italy|Ivory Coast|Jamaica|Japan|Jersey|
Jordan|Kazakhstan|Kenya|Kiribati|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|
Liberia|Libya|Liechtenstein|Lithuania|Luxembourg|Macau|Macao|Madagascar|Malawi|Malaysia|
Maldives|Mali|Malta|Marshall Islands|Mauritania|Mauritius|Mexico|Micronesia|Moldova|
Monaco|Mongolia|Montenegro|Morocco|Mozambique|Myanmar|Burma|Namibia|Nauru|Nepal|
Netherlands|New Zealand|Nicaragua|Niger|Nigeria|North Korea|North Macedonia|Macedonia|
Norway|Oman|Pakistan|Palau|Palestine|Panama|Papua New Guinea|Paraguay|Peru|Philippines|
Poland|Portugal|Puerto Rico|Qatar|Romania|Russia|Russian Federation|Rwanda|
Saint Kitts and Nevis|Saint Lucia|Saint Vincent and the Grenadines|Samoa|San Marino|
Sao Tome and Principe|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|
Slovakia|Slovenia|Solomon Islands|Somalia|South Africa|South Korea|South Sudan|Spain|
Sri Lanka|Sudan|Suriname|Sweden|Switzerland|Syria|Taiwan|Tajikistan|Tanzania|Thailand|
Timor-Leste|Togo|Tonga|Trinidad and Tobago|Tunisia|Turkey|Turkiye|Turkmenistan|Tuvalu|
Uganda|Ukraine|United Arab Emirates|United Kingdom|United States|United States of America|
Uruguay|Uzbekistan|Vanuatu|Vatican City|Venezuela|Vietnam|Viet Nam|Yemen|Zambia|Zimbabwe|
Great Britain|England|Scotland|Wales|Northern Ireland|UK|USA|UAE|British Virgin Islands
"""

_DEMONYMS = {
    "Afghan": "Afghanistan", "Albanian": "Albania", "Algerian": "Algeria",
    "American": "United States", "Angolan": "Angola", "Argentine": "Argentina",
    "Argentinian": "Argentina", "Armenian": "Armenia", "Australian": "Australia",
    "Austrian": "Austria", "Azerbaijani": "Azerbaijan", "Bahraini": "Bahrain",
    "Bangladeshi": "Bangladesh", "Belarusian": "Belarus", "Belgian": "Belgium",
    "Bolivian": "Bolivia", "Bosnian": "Bosnia and Herzegovina", "Brazilian": "Brazil",
    "British": "United Kingdom", "Bulgarian": "Bulgaria", "Burmese": "Myanmar",
    "Cambodian": "Cambodia", "Cameroonian": "Cameroon", "Canadian": "Canada",
    "Chilean": "Chile", "Chinese": "China", "Colombian": "Colombia",
    "Congolese": "Congo", "Croatian": "Croatia", "Cuban": "Cuba", "Cypriot": "Cyprus",
    "Czech": "Czech Republic", "Danish": "Denmark", "Dominican": "Dominican Republic",
    "Dutch": "Netherlands", "Ecuadorian": "Ecuador", "Ecuadorean": "Ecuador",
    "Egyptian": "Egypt", "Emirati": "United Arab Emirates", "English": "United Kingdom",
    "Eritrean": "Eritrea", "Estonian": "Estonia", "Ethiopian": "Ethiopia",
    "Filipino": "Philippines", "Finnish": "Finland", "French": "France",
    "Georgian": "Georgia", "German": "Germany", "Ghanaian": "Ghana", "Greek": "Greece",
    "Guatemalan": "Guatemala", "Haitian": "Haiti", "Honduran": "Honduras",
    "Hungarian": "Hungary", "Icelandic": "Iceland", "Indian": "India",
    "Indonesian": "Indonesia", "Iranian": "Iran", "Iraqi": "Iraq", "Irish": "Ireland",
    "Israeli": "Israel", "Italian": "Italy", "Ivorian": "Cote d'Ivoire",
    "Jamaican": "Jamaica", "Japanese": "Japan", "Jordanian": "Jordan",
    "Kazakh": "Kazakhstan", "Kenyan": "Kenya", "Korean": "South Korea",
    "Kosovar": "Kosovo", "Kuwaiti": "Kuwait", "Latvian": "Latvia",
    "Lebanese": "Lebanon", "Liberian": "Liberia", "Libyan": "Libya",
    "Lithuanian": "Lithuania", "Luxembourgish": "Luxembourg", "Malaysian": "Malaysia",
    "Maltese": "Malta", "Mexican": "Mexico", "Moldovan": "Moldova",
    "Mongolian": "Mongolia", "Montenegrin": "Montenegro", "Moroccan": "Morocco",
    "Mozambican": "Mozambique", "Namibian": "Namibia", "Nepalese": "Nepal",
    "Nepali": "Nepal", "New Zealander": "New Zealand", "Nicaraguan": "Nicaragua",
    "Nigerian": "Nigeria", "Nigerien": "Niger", "North Korean": "North Korea",
    "Norwegian": "Norway", "Omani": "Oman", "Pakistani": "Pakistan",
    "Palestinian": "Palestine", "Panamanian": "Panama", "Paraguayan": "Paraguay",
    "Peruvian": "Peru", "Philippine": "Philippines", "Polish": "Poland",
    "Portuguese": "Portugal", "Qatari": "Qatar", "Romanian": "Romania",
    "Russian": "Russia", "Rwandan": "Rwanda", "Saudi": "Saudi Arabia",
    "Scottish": "United Kingdom", "Senegalese": "Senegal", "Serbian": "Serbia",
    "Singaporean": "Singapore", "Slovak": "Slovakia", "Slovenian": "Slovenia",
    "Somali": "Somalia", "South African": "South Africa", "South Korean": "South Korea",
    "Spanish": "Spain", "Sri Lankan": "Sri Lanka", "Sudanese": "Sudan",
    "Swedish": "Sweden", "Swiss": "Switzerland", "Syrian": "Syria",
    "Taiwanese": "Taiwan", "Tanzanian": "Tanzania", "Thai": "Thailand",
    "Tunisian": "Tunisia", "Turkish": "Turkey", "Ugandan": "Uganda",
    "Ukrainian": "Ukraine", "Uruguayan": "Uruguay", "Uzbek": "Uzbekistan",
    "Venezuelan": "Venezuela", "Vietnamese": "Vietnam", "Welsh": "United Kingdom",
    "Yemeni": "Yemen", "Zambian": "Zambia", "Zimbabwean": "Zimbabwe",
}

_SYNONYMS = {
    "UK": "United Kingdom", "Great Britain": "United Kingdom", "England": "United Kingdom",
    "Scotland": "United Kingdom", "Wales": "United Kingdom",
    "Northern Ireland": "United Kingdom", "USA": "United States",
    "United States of America": "United States", "UAE": "United Arab Emirates",
    "Czechia": "Czech Republic", "Ivory Coast": "Cote d'Ivoire",
    "Russian Federation": "Russia", "Viet Nam": "Vietnam", "Burma": "Myanmar",
    "Macedonia": "North Macedonia", "Turkiye": "Turkey", "Macao": "Macau",
}

_CODES = {  # ISO-2 codes seen on records, for the canonical hint only
    "US": "United States", "GB": "United Kingdom", "NG": "Nigeria", "DE": "Germany",
    "ES": "Spain", "FR": "France", "IT": "Italy", "BD": "Bangladesh", "IE": "Ireland",
    "CA": "Canada", "NL": "Netherlands", "AU": "Australia", "KE": "Kenya", "AT": "Austria",
    "PK": "Pakistan", "ZA": "South Africa", "SG": "Singapore", "SE": "Sweden",
    "EC": "Ecuador", "PT": "Portugal", "RS": "Serbia", "BR": "Brazil", "DK": "Denmark",
    "TH": "Thailand", "PL": "Poland", "CH": "Switzerland", "NO": "Norway", "VN": "Vietnam",
    "GH": "Ghana", "PA": "Panama", "SY": "Syria", "HT": "Haiti", "CI": "Cote d'Ivoire",
    "ME": "Montenegro", "DZ": "Algeria", "IN": "India", "CZ": "Czech Republic",
    "ID": "Indonesia", "MX": "Mexico", "PE": "Peru", "AR": "Argentina", "RU": "Russia",
    "RO": "Romania", "NZ": "New Zealand", "KP": "North Korea", "CO": "Colombia",
    "AM": "Armenia", "MA": "Morocco", "JM": "Jamaica", "IL": "Israel", "LB": "Lebanon",
    "FI": "Finland", "SI": "Slovenia", "MD": "Moldova", "HU": "Hungary",
    "AE": "United Arab Emirates", "DO": "Dominican Republic", "CN": "China", "JP": "Japan",
    "UA": "Ukraine", "TR": "Turkey", "GR": "Greece", "BE": "Belgium", "LU": "Luxembourg",
}


def _fold(text: str) -> str:
    if not text.isascii():
        text = strip_accents(text)
    return text.strip().lower().rstrip(".")


_CANON: dict[str, str] = {}
for _name in (n.strip() for n in _COUNTRY_NAMES.replace("\n", "").split("|")):
    if _name:
        _CANON[_fold(_name)] = _SYNONYMS.get(_name, _name)
for _alias, _target in {**_DEMONYMS, **_SYNONYMS}.items():
    _CANON[_fold(_alias)] = _target
for _code, _target in _CODES.items():
    _CANON[_fold(_code)] = _target
_CANON["the netherlands"] = "Netherlands"
_CANON["the united kingdom"] = "United Kingdom"
_CANON["the united states"] = "United States"


def canonical_country(raw: str | None) -> str | None:
    """Best-effort canonical country name (never drops an unknown value).

    Uses the private lexicon above, then normalize_country. This is a HINT for
    callers; rules should normalise with normalize.countries themselves.
    """
    if not raw or not str(raw).strip():
        return None
    key = str(raw)
    if key not in _COUNTRY_CACHE:
        _COUNTRY_CACHE[key] = _canonical_country(key)
    return _COUNTRY_CACHE[key]


_COUNTRY_CACHE: dict[str, str | None] = {}


def _canonical_country(raw: str) -> str | None:
    folded = _fold(raw)
    if folded in _CANON:
        return _CANON[folded]
    if folded in _NOT_COUNTRIES or not any(ch.isalpha() for ch in folded):
        return None
    try:
        from .normalize.countries import normalize_country

        norm = normalize_country(raw)
    except Exception:  # noqa: BLE001 - a broken normaliser must not drop a country
        norm = None
    if norm:
        return _CANON.get(_fold(norm), norm)
    return str(raw).strip()


try:
    from .normalize.countries import NOT_COUNTRIES as _OFFICIAL_NOT_COUNTRIES
except Exception:  # noqa: BLE001  # pragma: no cover
    _OFFICIAL_NOT_COUNTRIES = frozenset()
_NOT_COUNTRIES = frozenset(_OFFICIAL_NOT_COUNTRIES) | {
    "international", "europe", "global", "worldwide", "unknown", "n/a", "na", "none", "null",
    "not known", "other", "various", "stateless", "xx", "xxx", "tbc", "-",
}


def _lexicon_terms(extra: list[str]) -> list[str]:
    terms = set()
    for name in (n.strip() for n in _COUNTRY_NAMES.replace("\n", "").split("|")):
        if name:
            terms.add(name)
    terms.update(_DEMONYMS)
    terms.update(_SYNONYMS)
    for value in extra:
        value = (value or "").strip()
        if len(value) >= 4 and value[0].isupper():
            terms.add(value)
    return sorted(terms, key=lambda t: (-len(t), t))


_BASE_TERMS = _lexicon_terms([])
_LEXICON_CACHE: dict[tuple[str, ...], re.Pattern[str]] = {}


def _lexicon_regex(extra: tuple[str, ...] = ()) -> re.Pattern[str]:
    pat = _LEXICON_CACHE.get(extra)
    if pat is None:
        terms = _lexicon_terms(list(extra)) if extra else _BASE_TERMS
        alt = "|".join(re.escape(t) for t in terms)
        pat = re.compile(rf"(?<![\w'])(?:{alt})(?![\w'])", re.IGNORECASE)
        _LEXICON_CACHE[extra] = pat
    return pat


def find_countries(text: str, extra: tuple[str, ...] = (), skip: list[tuple[int, int]] | None = None
                   ) -> list[str]:
    """Country names / demonyms mentioned in ``text`` (raw, in order, distinct)."""
    out: list[str] = []
    for m in _lexicon_regex(extra).finditer(text or ""):
        if skip and any(a <= m.start() < b for a, b in skip):
            continue
        value = m.group(0)
        if not value[0].isupper():  # "turkey", "chad" in running text are not countries
            continue
        if value not in out:
            out.append(value)
    return out


# ------------------------------------------------------------------ notes parsing

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE_PATTERNS = (
    r"\d{4}-\d{1,2}-\d{1,2}",
    r"\d{1,2}[/.]\d{1,2}[/.]\d{4}",
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_ALT})\.?,?\s+\d{{4}}",
    rf"(?:{_MONTH_ALT})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}",
    rf"(?:{_MONTH_ALT})\.?\s+\d{{4}}",
    r"\d{4}-\d{1,2}",
)
_DATE_RE = re.compile(r"(?<![\w/.-])(?:" + "|".join(_DATE_PATTERNS) + r")(?![\w/-])", re.IGNORECASE)
_ISO_RE = re.compile(r"(?<![\w-])(\d{4}-\d{2}-\d{2})(?![\w-])")
_BIRTH_ANCHOR = re.compile(
    r"\b(?:D\.?O\.?B\.?|born|birth\w*|date\s+of\s+birth)(?![\w])", re.IGNORECASE
)
_BARE_YEAR = re.compile(r"(?<![\w/.-])(?:1[89]\d\d|20\d\d)(?![\w/-])")
_NEGATIVE = re.compile(
    r"\b(?:reject\w*|fail\w*|expired?|declin\w*|denied|unsuccessful\w*|unable\s+to\s+verify|"
    r"could\s+not\s+(?:be\s+)?verif\w*|not\s+(?:be\s+)?verified|mismatch\w*|invalid|"
    r"suspend\w*|revoked|fraudulent|forged|pending|incomplete|inconclusive|"
    r"did\s+not\s+pass|not\s+approved|no\s+match)\b",
    re.IGNORECASE,
)
_VERIFY = re.compile(
    r"\b(?:verif\w*|validat\w*|confirm\w*|approv\w*|pass\w*|clear\w*|vett\w*|check\w*|"
    r"screen\w*|process\w*|authenticat\w*|complet\w*|conclud\w*|success\w*|KYC|identity)\b",
    re.IGNORECASE,
)
_PROVIDER = re.compile(r"\b(Persona|Onfido|Veriff|Sumsub|Jumio|Trulioo|IDnow|Shufti\w*|Yoti)\b")
_DOC = re.compile(
    r"\b(passport|national\s+id(?:entity)?(?:\s+card)?|identity\s+card|id\s+card|"
    r"driv(?:ing|er'?s)\s+licen[cs]e|residence\s+permit|residence\s+card)\b",
    re.IGNORECASE,
)
_NOT_NAME = frozenset({
    "ID", "DOB", "KYC", "AML", "PEP", "UK", "US", "USA", "UAE", "EU", "SSN", "NI", "ISO",
    "IBAN", "API", "OK", "SR", "NOTE", "CDD", "EDD", "SOF", "SOW",
})
# kept inside a name as written (the record shows them too) but never a name on their own
_PLACEHOLDER_WORDS = frozenset({"N/A", "NA", "XXX", "TEST", "UNKNOWN", "NONE", "NULL", "TBC"})
_RESIDENCE = re.compile(
    r"\b(?:resid\w*|lived|live[sd]?|living|home|household|dwell\w*|stay\w*|based|base|"
    r"employ\w*|work\w*|job|jobs|career|professional\w*|occupation|position|income|"
    r"relocat\w*|moved|life|business|labou?r|establish\w*|settled|tenure)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerifiedIdentity:
    """One approved identity check (KYC provider record or onboarding note)."""

    source_id: str  # KYC-... or NOTE-... (cite this as evidence)
    kind: str  # "kyc_check" | "note"
    checked_at: str | None  # ISO date of the check (note: date in the text, else created_at)
    name: str | None = None
    dob_raw: str | None = None
    dob: PartialDate | None = None
    document_country: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    provider: str | None = None

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.checked_at or "", self.source_id)


@dataclass(frozen=True)
class CountryClaim:
    """A country tied to the person, raw as written, with the record that says so."""

    raw: str
    source_id: str  # CUS-..., KYC-... or NOTE-...
    kind: str  # "residence" | "nationality" | "document" | "note_residence"
    canonical: str | None = None  # hint from canonical_country(); rules may re-normalise


def _is_upper_word(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _uppercase_name(text: str, skip: list[tuple[int, int]]) -> tuple[str | None, tuple[int, int] | None]:
    """The longest run of UPPER-CASE words (the verified name), with its span."""
    runs: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\S+", text):
        raw = m.group(0)
        word = raw.strip(",;:()[]\"!?")
        if word.endswith(("'s", "’s")):
            word = word[:-2]
        word = word.rstrip(".'’")
        start = m.start() + raw.find(word[:1]) if word else m.start()
        if word and _is_upper_word(word) and not any(a <= start < b for a, b in skip):
            current.append((start, start + len(word), word))
            breaks = raw[raw.find(word) + len(word):] if word in raw else ""
            if any(ch in breaks for ch in ",;:()."):  # punctuation after the word ends the run
                runs.append(current)
                current = []
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    best: tuple[int, int] | None = None
    best_run: list[tuple[int, int, str]] = []
    for run in runs:
        while run and run[0][2] in _NOT_NAME:  # "ID", "DOB" at the edges are not the name
            run = run[1:]
        while run and run[-1][2] in _NOT_NAME:
            run = run[:-1]
        real = [w for w in run if w[2] not in _NOT_NAME and w[2] not in _PLACEHOLDER_WORDS
                and sum(ch.isalpha() for ch in w[2]) >= 2]
        if not real:
            continue
        letters = sum(len(w[2]) for w in run)
        if letters < 3:
            continue
        score = (len(real), letters)  # most real words, then longest, then first
        if best is None or score > best:
            best, best_run = score, run
    if not best_run:
        return None, None
    return " ".join(w[2] for w in best_run), (best_run[0][0], best_run[-1][1])


def _dob_span(text: str, created_at: str | None) -> tuple[int, int, str] | None:
    """The date that is the date of birth: right after a birth anchor ("DOB 1990-01-02",
    "born on ..."), right before one ("... 1 June 1959 as the date of birth"), or in
    brackets ("NAME (19/12/2003)"). The check date (an ISO date after "on", or the
    note's own date) is never the DOB."""
    dates = [(m.start(), m.end(), m.group(0)) for m in _DATE_RE.finditer(text)]
    covered = [(d[0], d[1]) for d in dates]
    years = [(m.start(), m.end(), m.group(0)) for m in _BARE_YEAR.finditer(text)
             if not any(a <= m.start() < b for a, b in covered)]
    if not dates and not years:
        return None
    created = str(created_at or "")[:10]

    def is_check_date(d: tuple[int, int, str]) -> bool:
        if created and d[2] == created:
            return True
        if not re.search(r"\bon\s+(?:the\s+)?$", text[max(0, d[0] - 8):d[0]], re.IGNORECASE):
            return False
        # "born on <date>" / "born in Bangladesh on <date>" is still the birth date
        return not _BIRTH_ANCHOR.search(text[max(0, d[0] - 30):d[0]])

    anchors = list(_BIRTH_ANCHOR.finditer(text))
    for anchor in anchors:  # "DOB <date>", "born on <date>", "date of birth of <date>"
        after = [d for d in dates if anchor.end() <= d[0] <= anchor.end() + 40 and not is_check_date(d)]
        if after:
            return after[0]
    for anchor in anchors:  # "<date> birth date", "lists <date> as the date of birth"
        before = [d for d in dates if 0 <= anchor.start() - d[1] <= 30 and not is_check_date(d)]
        if before:
            return before[-1]
    for d in dates:  # "NAME (19/12/2003)"
        if text[max(0, d[0] - 1):d[0]] == "(" and text[d[1]:d[1] + 1] == ")" and not is_check_date(d):
            return d
    for anchor in anchors:  # a year alone: "born 1966", "date of birth of 1974"
        after = [y for y in years if anchor.end() <= y[0] <= anchor.end() + 15]
        if after:
            return after[0]
    return None


def parse_kyc_note(text: str | None, created_at: str | None = None, note_id: str = "",
                   *, require_success: bool = True) -> VerifiedIdentity | None:
    """Read a successful identity verification out of a free-text note.

    Anchor-based, not template-based: a date after a birth anchor (DOB / born /
    date of birth) is the DOB; the longest UPPER-CASE word run is the verified name;
    a country name or demonym from the lexicon is the document country; the ISO date
    that is not the DOB is the check date (else ``created_at``).

    Returns None unless the note reads as a verification (verification wording,
    a name and a DOB) and nothing in it says the check was rejected, failed,
    expired or could not be completed.
    """
    if not text:
        return None
    if require_success and _NEGATIVE.search(text):
        return None
    if not (_VERIFY.search(text) or _PROVIDER.search(text) or _DOC.search(text)):
        return None
    dob_span = _dob_span(text, created_at)
    if dob_span is None:
        return None
    dob_raw = re.sub(r"\s+", " ", dob_span[2]).strip()
    dob = parse_dob(re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", dob_raw))
    checked_at = None
    for m in _ISO_RE.finditer(text):
        if not (dob_span[0] <= m.start() < dob_span[1]):
            checked_at = m.group(1)
            break
    if checked_at is None and created_at:
        checked_at = str(created_at)[:10]
    skip = [(dob_span[0], dob_span[1])]
    name, name_span = _uppercase_name(text, skip)
    if not name:
        return None
    if name_span:
        skip.append(name_span)
    provider_m = _PROVIDER.search(text)
    if provider_m:
        skip.append((provider_m.start(), provider_m.end()))
    countries = find_countries(text, skip=skip)
    doc_m = _DOC.search(text)
    return VerifiedIdentity(
        source_id=note_id,
        kind="note",
        checked_at=checked_at,
        name=name,
        dob_raw=dob_raw,
        dob=dob,
        document_country=countries[0] if countries else None,
        document_type=re.sub(r"\s+", " ", doc_m.group(1)).lower() if doc_m else None,
        provider=provider_m.group(1) if provider_m else None,
    )


def extract_residence_countries(text: str | None, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Countries a note says the customer lived or worked in (raw, distinct, in order).

    Wording-agnostic: a residence / employment word plus a country name or demonym.
    Over-inclusion is the safe direction (a bigger customer country set can only
    block location clears, never cause one).
    """
    if not text or not _RESIDENCE.search(text):
        return ()
    return tuple(find_countries(text, extra))


def kyc_identity(check: KycCheck) -> VerifiedIdentity:
    return VerifiedIdentity(
        source_id=check.check_id,
        kind="kyc_check",
        checked_at=(str(check.checked_at)[:10] if check.checked_at else None),
        name=check.verified_name,
        dob_raw=check.verified_dob,
        dob=parse_dob(check.verified_dob),
        document_country=check.document_country,
        document_type=check.document_type,
        document_number=check.document_number,
        provider=check.provider,
    )


# ------------------------------------------------------------------ identity graph


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        parent = self.parent
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # smallest id becomes the root: deterministic regardless of insertion order
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


@dataclass(frozen=True)
class MergeEdge:
    a: str
    b: str
    why: str  # "phone+dob" | "document"
    detail: str  # the shared phone digits / document number


class IdentityGraph:
    """Union-find over all customer records, built once per Dataset."""

    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self._uf = _UnionFind()
        self.edges: list[MergeEdge] = []
        self.approved_by_customer: dict[str, tuple[KycCheck, ...]] = {}
        self.applicants_by_customer: dict[str, tuple[tuple[str, str], ...]] = {}
        self._build()
        groups: dict[str, list[str]] = {}
        for rid in sorted(ds.customers):
            groups.setdefault(self._uf.find(rid), []).append(rid)
        self._groups = {root: tuple(members) for root, members in groups.items()}

    # -- KYC reached through onboarding: customer -> accounts -> onboarding -> applicant
    def _index_kyc(self) -> None:
        ds = self.ds
        pairs = {(a.account_id, a.customer_id) for a in ds.accounts}
        approved: dict[str, set[str]] = {}
        applicants: dict[str, set[tuple[str, str]]] = {}
        for ob in ds.onboarding:
            if not ob.kyc_applicant_id or not ob.customer_id:
                continue
            if pairs and (ob.account_id, ob.customer_id) not in pairs:
                continue
            applicants.setdefault(ob.customer_id, set()).add((ob.account_id, ob.kyc_applicant_id))
            for chk in ds.kyc_by_applicant.get(ob.kyc_applicant_id, []):
                if is_approved(chk):
                    approved.setdefault(ob.customer_id, set()).add(chk.check_id)
        self.approved_by_customer = {
            cid: tuple(sorted((ds.kyc_checks[i] for i in ids),
                              key=lambda c: (c.checked_at or "", c.check_id), reverse=True))
            for cid, ids in sorted(approved.items())
        }
        self.applicants_by_customer = {cid: tuple(sorted(v)) for cid, v in sorted(applicants.items())}

    def _build(self) -> None:
        ds = self.ds
        self._index_kyc()
        by_phone: dict[str, list[str]] = {}
        for rid in sorted(ds.customers):
            phone = normalize_phone(ds.customers[rid].phone)
            if phone:
                by_phone.setdefault(phone, []).append(rid)
        by_phone_dob: dict[tuple[str, PartialDate], list[str]] = {}
        for phone, rids in sorted(by_phone.items()):
            if len(rids) < 2:
                continue
            for rid in rids:
                dob = parse_dob(ds.customers[rid].dob)
                if is_full(dob):
                    by_phone_dob.setdefault((phone, dob), []).append(rid)
        for (phone, _dob), ids in sorted(by_phone_dob.items(), key=lambda kv: kv[1][0]):
            for other in ids[1:]:
                self._uf.union(ids[0], other)
                self.edges.append(MergeEdge(ids[0], other, "phone+dob", phone))
        by_doc: dict[str, list[str]] = {}
        for cid in sorted(self.approved_by_customer):
            if cid not in ds.customers:
                continue
            for chk in self.approved_by_customer[cid]:
                doc = normalize_document(chk.document_number)
                if len(doc) >= 4 and cid not in by_doc.setdefault(doc, []):
                    by_doc[doc].append(cid)
        for doc, ids in sorted(by_doc.items()):
            ids = sorted(ids)
            for other in ids[1:]:
                self._uf.union(ids[0], other)
                self.edges.append(MergeEdge(ids[0], other, "document", doc))

    def group(self, record_id: str) -> tuple[str, ...]:
        """All record ids of the person this record belongs to (sorted)."""
        if record_id not in self.ds.customers:
            return (record_id,)
        return self._groups.get(self._uf.find(record_id), (record_id,))

    def group_key(self, record_id: str) -> str:
        return self.group(record_id)[0]


def identity_graph(ds: Dataset) -> IdentityGraph:
    graph = getattr(ds, "_identity_graph", None)
    if graph is None or graph.ds is not ds:
        graph = IdentityGraph(ds)
        try:
            object.__setattr__(ds, "_identity_graph", graph)
        except Exception:  # noqa: BLE001 - caching is an optimisation only  # pragma: no cover
            pass
    return graph


# ------------------------------------------------------------------ per-person facts

_NOTE_CACHE_ATTR = "_parsed_notes_cache"


def _note_cache(ds: Dataset) -> dict[str, tuple[VerifiedIdentity | None, tuple[str, ...]]]:
    cache = getattr(ds, _NOTE_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            object.__setattr__(ds, _NOTE_CACHE_ATTR, cache)
        except Exception:  # noqa: BLE001  # pragma: no cover
            pass
    return cache


def read_note(ds: Dataset, note: Note) -> tuple[VerifiedIdentity | None, tuple[str, ...]]:
    """(verified identity if the note records a successful check, residence countries)."""
    cache = _note_cache(ds)
    hit = cache.get(note.note_id)
    if hit is not None:
        return hit
    ident = parse_kyc_note(note.text, note.created_at, note.note_id)
    channel = _key(note.channel)
    if ident is not None and channel not in {"", "kyc", "onboarding", "verification"}:
        # an identity statement outside the onboarding team's channel: only accept it
        # when it names a provider (still a verification record), else ignore it
        if not ident.provider:
            ident = None
    residence: tuple[str, ...] = ()
    if ident is None:
        residence = extract_residence_countries(note.text)
    result = (ident, residence)
    cache[note.note_id] = result
    return result


def verified_identities(ds: Dataset, checks: list[KycCheck], notes: list[Note]
                        ) -> tuple[VerifiedIdentity, ...]:
    """All approved identity checks of a person, newest first (ties: higher id first)."""
    out = [kyc_identity(c) for c in checks if is_approved(c)]
    for note in notes:
        ident, _ = read_note(ds, note)
        if ident is not None:
            out.append(ident)
    return tuple(sorted(out, key=lambda v: v.sort_key, reverse=True))


def country_claims(ds: Dataset, records: list[Customer], identities: tuple[VerifiedIdentity, ...],
                   notes: list[Note]) -> tuple[CountryClaim, ...]:
    claims: list[CountryClaim] = []
    for rec in sorted(records, key=lambda r: r.record_id):
        if rec.residence_country:
            claims.append(CountryClaim(rec.residence_country, rec.record_id, "residence",
                                       canonical_country(rec.residence_country)))
        for nat in rec.nationality:
            claims.append(CountryClaim(nat, rec.record_id, "nationality", canonical_country(nat)))
    for ident in sorted(identities, key=lambda v: v.source_id):
        if ident.document_country:
            claims.append(CountryClaim(ident.document_country, ident.source_id, "document",
                                       canonical_country(ident.document_country)))
    for note in sorted(notes, key=lambda n: n.note_id):
        _, residence = read_note(ds, note)
        for raw in residence:
            claims.append(CountryClaim(raw, note.note_id, "note_residence", canonical_country(raw)))
    seen: set[tuple[str, str, str]] = set()
    unique: list[CountryClaim] = []
    for c in claims:
        k = (c.raw, c.source_id, c.kind)
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return tuple(unique)
