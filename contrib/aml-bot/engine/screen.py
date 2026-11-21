#!/usr/bin/env python3
"""Track 2 identity screening, round 1. Deterministic, standard library only."""
import json, os, re, sys, unicodedata
from datetime import date

# ---------------------------------------------------------------- loading
LOAD_WARNINGS = []

# Field-name variants we accept (left) for the schema names the code uses (right). Keys are compared after
# lower-casing and turning spaces/hyphens into underscores.
FIELD_ALIASES = {
    "alerts.jsonl": {"id": "alert_id", "alertid": "alert_id", "screening_date": "screened_at", "screened_on": "screened_at",
                     "date_screened": "screened_at", "name": "customer_name", "screened_name": "customer_name",
                     "type": "entity_type", "date_of_birth": "dob", "birth_date": "dob", "account": "account_ref",
                     "account_id": "account_ref", "account_number": "account_ref", "login": "login_handle",
                     "username": "login_handle", "customer_email": "email", "hits": "hit_ids", "hit_id_list": "hit_ids"},
    "hits.jsonl": {"id": "hit_id", "hitid": "hit_id", "score": "match_score", "type": "entity_type", "full_name": "name",
                   "alias": "aliases", "aka": "aliases", "dob": "dobs", "dates_of_birth": "dobs", "date_of_birth": "dobs",
                   "birth_dates": "dobs", "country": "countries", "nationalities": "nationality",
                   "address": "addresses", "pob": "place_of_birth", "date_of_death": "deceased_date",
                   "death_date": "deceased_date", "category": "categories", "notes": "profile_notes",
                   "profile": "profile_notes", "status": "life_status"},
    "customers.jsonl": {"id": "record_id", "customer_id": "record_id", "type": "entity_type", "name": "full_name",
                        "date_of_birth": "dob", "birth_date": "dob", "country": "residence_country",
                        "country_of_residence": "residence_country", "residence": "residence_country",
                        "email_address": "email", "phone_number": "phone", "mobile": "phone"},
    "accounts.jsonl": {"id": "account_id", "account": "account_id", "record_id": "customer_id", "login": "login_handle",
                       "username": "login_handle"},
    "onboarding.jsonl": {"record_id": "customer_id", "applicant_id": "kyc_applicant_id", "kyc_id": "kyc_applicant_id"},
    "kyc_checks.jsonl": {"id": "check_id", "kyc_applicant_id": "applicant_id", "name": "verified_name",
                         "dob": "verified_dob", "date_of_birth": "verified_dob", "status": "result",
                         "outcome": "result", "country": "document_country", "doc_country": "document_country",
                         "doc_number": "document_number", "checked_on": "checked_at", "date": "checked_at"},
    "notes.jsonl": {"id": "note_id", "email": "requester_email", "customer_email": "requester_email", "body": "text",
                    "note": "text", "content": "text", "date": "created_at"},
    "search_results.jsonl": {"id": "result_id", "q": "query", "text": "snippet", "summary": "snippet"},
}
KEEP_LIST = {"hit_ids", "reason_codes", "customer_record_ids", "evidence"}

def _flatten(v):
    """Lists and dicts from a richer schema become the '; '-separated strings the rules read."""
    if isinstance(v, dict):
        tag = v.get("type") or v.get("tag") or v.get("kind")
        vals = [str(x) for k, x in v.items() if k not in ("type", "tag", "kind") and x not in (None, "")]
        return ", ".join(vals) + (f" [{tag}]" if tag else "")
    if isinstance(v, list):
        return "; ".join(_flatten(x) for x in v if x not in (None, ""))
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return str(v) if not float(v).is_integer() else str(int(v))
    return str(v).strip()

def normalise_record(name, r):
    aliases = FIELD_ALIASES.get(name, {})
    out = {}
    for k, v in r.items():
        k2 = re.sub(r"[\s\-]+", "_", str(k).strip().lower())
        k2 = aliases.get(k2, k2)
        if k2 in out and out[k2] not in (None, ""):
            continue                       # never let an alias overwrite a real field
        if k2 in KEEP_LIST:
            out[k2] = [str(x).strip() for x in v] if isinstance(v, list) else v
        else:
            val = _flatten(v)
            out[k2] = None if val in ("", "null", "None", "NULL") else val
    return out

def load_jsonl(folder, name):
    """Tolerant JSON Lines reader: bad lines, BOMs, encoding errors and field-name variants never stop a run."""
    p = os.path.join(folder, name)
    out = []
    if not os.path.exists(p):
        LOAD_WARNINGS.append(f"{name}: missing")
        return out
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                LOAD_WARNINGS.append(f"{name}:{i}: bad JSON line skipped")
                continue
            if isinstance(r, dict):
                out.append(normalise_record(name, r))
            else:
                LOAD_WARNINGS.append(f"{name}:{i}: not an object, skipped")
    return out

def as_list(v):
    """Accept a list or a '; '-separated string (workbook export)."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return [x.strip() for x in str(v).split(";") if x.strip()]

# ---------------------------------------------------------------- dates
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

AMBIGUOUS_DMY_POLICY = "dmy"   # dd/mm/yyyy is this bank's convention; set "year" to keep only the year when unsure

def _valid(y, m, d):
    if not 1850 <= y <= 2035:
        return None
    if m is not None and not 1 <= m <= 12:
        return None
    if d is not None:
        if m is None:
            return None
        import calendar
        if not 1 <= d <= calendar.monthrange(y, m)[1]:
            return None
    return (y, m, d)

def parse_date(s, ambiguous=None):
    """Return (year, month|None, day|None) or None (= unknown). Keeps partial dates partial.
    Fail-safe: anything ambiguous or unrecognised (e.g. 2-digit years) is unknown, never a guessed date."""
    if s is None or isinstance(s, (list, dict, bool)):
        return None
    if isinstance(s, (int, float)):
        s = str(int(s))
    s = str(s).strip()
    s = re.sub(r"[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$", "", s)   # drop a time part
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = re.sub(r"\s+", " ", s.replace(",", " ")).strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?", s)
    if m:
        return _valid(int(m[1]), int(m[2]) if m[2] else None, int(m[3]) if m[3] else None)
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 and b <= 12:
            return _valid(y, b, a)
        if b > 12 and a <= 12:                     # unmistakably month-first
            return _valid(y, a, b)
        if a == b:
            return _valid(y, a, b)
        return _valid(y, b, a) if (ambiguous or AMBIGUOUS_DMY_POLICY) == "dmy" else _valid(y, None, None)
    m = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}) (\d{4})", s) or re.fullmatch(r"([A-Za-z]+)\.?-(\d{1,2})-(\d{4})", s)
    if m and m[1].lower() in MONTHS:                                   # Month d yyyy
        return _valid(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    m = re.fullmatch(r"(\d{1,2})[ \-]([A-Za-z]+)\.?[ \-](\d{4})", s)
    if m and m[2].lower() in MONTHS:                                   # d Month yyyy
        return _valid(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    m = re.fullmatch(r"([A-Za-z]+)\.?[ \-](\d{4})", s)
    if m and m[1].lower() in MONTHS:                                   # Month yyyy
        return _valid(int(m[2]), MONTHS[m[1].lower()], None)
    return None                                                        # unknown: never a guess

_SLASH_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")

def detect_day_first(*tables):
    """Customer-side dd/mm vs mm/dd: if any customer-side date is unmistakably month-first (e.g. 03/25/1980),
    the convention is unknown, so ambiguous dates like 03/04/1980 keep only their year (never a guessed month).
    Hit-side dates are always parsed that way: the listing data mixes both conventions."""
    global AMBIGUOUS_DMY_POLICY
    month_first = 0
    for rows in tables:
        for r in rows:
            for v in r.values():
                if isinstance(v, str) and ("/" in v or "." in v or "-" in v):
                    for m in _SLASH_RE.finditer(v):
                        month_first += int(m[2]) > 12 and int(m[1]) <= 12
    AMBIGUOUS_DMY_POLICY = "year" if month_first else "dmy"
    return AMBIGUOUS_DMY_POLICY

def fmt_date(d):
    y, m, dd = d
    return f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{dd:02d}" if m and dd else "")

def dob_agrees(c, h):
    """Policy P7: same year and every part both give is equal."""
    if c[0] != h[0]:
        return False
    if c[1] and h[1] and c[1] != h[1]:
        return False
    if c[1] and h[1] and c[2] and h[2] and c[2] != h[2]:
        return False
    return True

def dob_contradicts(c, h):
    """Policy P6: year more than 1 apart, or same year and months (or days) differ."""
    if abs(c[0] - h[0]) > 1:
        return True
    if c[0] == h[0] and c[1] and h[1]:
        if c[1] != h[1]:
            return True
        if c[2] and h[2] and c[2] != h[2]:
            return True
    return False

def date_before(a, b):
    """True only if partial date a is certainly before date b (full)."""
    ay, am, ad = a
    by, bm, bd = b
    if ay != by:
        return ay < by
    if am is None or bm is None:
        return False
    if am != bm:
        return am < bm
    if ad is None or bd is None:
        return False
    return ad < bd

# ---------------------------------------------------------------- countries
COUNTRY = {
 "AF":"afghanistan","AL":"albania","DZ":"algeria","AR":"argentina|argentine|argentinian","AM":"armenia|armenian",
 "AU":"australia|australian","AT":"austria|austrian","AZ":"azerbaijan","BH":"bahrain","BD":"bangladesh|bangladeshi",
 "BY":"belarus|belarusian","BE":"belgium|belgian","BA":"bosnia and herzegovina","BR":"brazil|brazilian",
 "VG":"british virgin islands","CM":"cameroon","CA":"canada|canadian","CN":"china|chinese","CO":"colombia|colombian",
 "CR":"costa rica","HR":"croatia|croatian","CZ":"czech republic|czechia|czech","CI":"cote d'ivoire|ivory coast|ivorian",
 "DK":"denmark|danish","DO":"dominican republic|dominican","EC":"ecuador|ecuadorian","EG":"egypt|egyptian",
 "ER":"eritrea","EE":"estonia","ET":"ethiopia","FI":"finland|finnish","FR":"france|french","GE":"georgia",
 "DE":"germany|german","GH":"ghana|ghanaian","GT":"guatemala","HT":"haiti|haitian","HN":"honduras",
 "HU":"hungary|hungarian","IS":"iceland","IN":"india|indian","ID":"indonesia|indonesian","IR":"iran|iranian",
 "IQ":"iraq|iraqi","IE":"ireland|irish","IL":"israel|israeli","IT":"italy|italian","JM":"jamaica|jamaican",
 "JP":"japan|japanese","JE":"jersey","JO":"jordan","KE":"kenya|kenyan","XK":"kosovo","KW":"kuwait","LV":"latvia",
 "LB":"lebanon|lebanese","LI":"liechtenstein","LU":"luxembourg","MO":"macao special administrative region of china|macao|macau",
 "MY":"malaysia","MX":"mexico|mexican","MD":"moldova","ME":"montenegro","MA":"morocco|moroccan","NP":"nepal",
 "NL":"netherlands|dutch","NZ":"new zealand","NI":"nicaragua","NG":"nigeria|nigerian","KP":"north korea",
 "MP":"northern mariana islands","NO":"norway|norwegian","OM":"oman","PK":"pakistan|pakistani","PS":"palestina|palestine",
 "PA":"panama","PY":"paraguay","PE":"peru|peruvian","PH":"philippines","PL":"poland|polish","PT":"portugal|portuguese",
 "PR":"puerto rico","QA":"qatar","RO":"romania|romanian","RU":"russia|russian federation|russian","SA":"saudi arabia",
 "RS":"serbia|serbian","SL":"sierra leone","SG":"singapore|singaporean","SI":"slovenia","SO":"somalia",
 "ZA":"south africa|south african","KR":"south korea","ES":"spain|spanish","SD":"sudan","SE":"sweden|swedish",
 "CH":"switzerland|swiss","SY":"syria","TH":"thailand|thai","TT":"trinidad and tobago","TR":"turkey|turkiye",
 "UG":"uganda","UA":"ukraine","AE":"united arab emirates|uae","GB":"united kingdom|british|uk|great britain",
 "US":"united states|american|usa|united states of america","UY":"uruguay","VE":"venezuela","VN":"vietnam|viet nam",
 "MK":"north macedonia|macedonia|macedonian","SK":"slovakia|slovak","SS":"south sudan","LT":"lithuania|lithuanian",
 "KZ":"kazakhstan","UZ":"uzbekistan","TM":"turkmenistan","KG":"kyrgyzstan","TJ":"tajikistan",
}
# Countries that were one state until recently: a location "mismatch" between them is not a safe clearance.
FORMER_STATES = {
    "Yugoslavia (and Serbia and Montenegro)": {"RS", "ME", "XK", "BA", "HR", "SI", "MK"},
    "the Soviet Union": {"RU", "UA", "BY", "MD", "GE", "AM", "AZ", "KZ", "UZ", "TM", "KG", "TJ", "EE", "LV", "LT"},
    "Czechoslovakia": {"CZ", "SK"},
    "Sudan (before South Sudan's independence)": {"SD", "SS"},
    "Ethiopia (before Eritrea's independence)": {"ET", "ER"},
}

def shared_former_state(a, b):
    for name, members in FORMER_STATES.items():
        ca, cb = set(a) & members, set(b) & members
        if ca and cb:
            return name, sorted(ca), sorted(cb)
    return None
_CMAP = {}
for code, names in COUNTRY.items():
    _CMAP[code.lower()] = code
    for n in names.split("|"):
        _CMAP[n] = code
try:   # full ISO 3166 table with official names, ISO-3 codes and nationalities (generated once, shipped with the code)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries.json"), encoding="utf-8") as _fh:
        for _code, _names in json.load(_fh).items():
            for _n in _names:
                _CMAP.setdefault(_n, _code)
except (OSError, ValueError):
    pass
NOT_COUNTRIES = {"international", "europe", "n/a", "na", "unknown", "none", "null", "", "-", "worldwide", "global",
                 "africa", "asia", "middle east", "latin america", "eu", "european union", "various"}
UNKNOWN_COUNTRY = "?"

def fold(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()

def _lookup(k):
    k = re.sub(r"\s+", " ", re.sub(r"^the ", "", k.strip(" .")))
    return _CMAP.get(k) or _CMAP.get(k.replace(".", ""))

def country_code(s, scan=False):
    """ISO-2 code; None if empty or not a country ('International'); UNKNOWN_COUNTRY if text we cannot recognise.
    Unknown never drives a location clearance."""
    if s is None or isinstance(s, (list, dict)):
        return None
    s = re.sub(r"\[.*?\]", "", str(s)).strip()
    if not s:
        return None
    k = fold(s.split(",")[-1])
    if k in NOT_COUNTRIES:
        return None
    code = _lookup(k)
    if code:
        return code
    if scan:                     # an address whose last part is not a country: look for a country name inside it
        found = {c for c in (_lookup(m.group(0)) for m in _NAME_SCAN_RE.finditer(fold(s))) if c}
        if len(found) == 1:
            return found.pop()
    return UNKNOWN_COUNTRY

def country_name(code):
    n = COUNTRY.get(code)
    if n:
        return n.split("|")[0].title()
    names = [k for k, v in _CMAP.items() if v == code and len(k) > 3]
    return min(names, key=len).title() if names else code

_NAME_SCAN_RE = re.compile(r"\b(" + "|".join(sorted({re.escape(k) for k in _CMAP if len(k) > 3}, key=len, reverse=True))
                           + r")\b")

# ---------------------------------------------------------------- names
PLACEHOLDERS = {"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbc", "tba"}

def normalise_variants(nv):
    """Accept {'given_name_groups': [...], 'company_legal_forms': [...]} or close variants (lists, dict of groups)."""
    groups, forms = [], []
    if isinstance(nv, list):
        groups = nv
    elif isinstance(nv, dict):
        for k, v in nv.items():
            kl = k.lower()
            if "legal" in kl or "form" in kl or "suffix" in kl:
                forms = list(v) if isinstance(v, list) else []
            elif isinstance(v, list) and v and isinstance(v[0], list):
                groups.extend(v)
            elif isinstance(v, list):
                groups.append([k] + [str(x) for x in v])
            elif isinstance(v, dict):
                groups.extend(list(x) for x in v.values() if isinstance(x, list))
    return {"given_name_groups": [[str(x) for x in g] for g in groups if isinstance(g, list)],
            "company_legal_forms": [str(x) for x in forms]}

class Names:
    def __init__(self, variants):
        self.group, self.members = {}, {}
        for gi, g in enumerate(variants.get("given_name_groups", [])):
            for n in g:
                self.group.setdefault(fold(n), set()).add(gi)
                self.members.setdefault(gi, []).append(fold(n))
        self.legal = {fold(x) for x in variants.get("company_legal_forms", [])}

    def parts(self, name, company=False):
        s = fold(name)
        s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
        toks = re.split(r"[^a-z0-9/]+", s.replace("'", "").replace("’", ""))
        out = []
        for t in toks:
            t = t.strip("/")
            if not t or t in PLACEHOLDERS or re.fullmatch(r"[\d/]+", t) or len(t) == 1:
                continue
            if company and t in self.legal:
                continue
            if t not in out:
                out.append(t)
        return out

    @staticmethod
    def edit(a, b):
        if abs(len(a) - len(b)) > 2:
            return 9
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]

    def part_match(self, a, b):
        if a == b:
            return True
        if self.group.get(a, set()) & self.group.get(b, set()):
            return True
        n = min(len(a), len(b))
        if n < 4:
            return False
        lim = 1 if n <= 6 else 2
        return self.edit(a, b) <= lim

    def variant_link(self, a, b):
        """Wider than part_match, used only to BLOCK clearances: a misspelling of one variant of a name
        (Christohper) still links to the other variants of its group (Cristobal)."""
        if self.part_match(a, b):
            return True
        for x, y in ((a, b), (b, a)):
            for g in self.group.get(y, set()):
                for member in self.members.get(g, ()):
                    n = min(len(x), len(member))
                    if n >= 4 and self.edit(x, member) <= (1 if n <= 6 else 2):
                        return True
        return False

    def agree(self, n1, n2, company=False):
        p1, p2 = self.parts(n1, company), self.parts(n2, company)
        if not p1 or not p2:
            return False
        # parts written together: try joining adjacent parts on each side
        return self._match(p1, p2)[0]

    def shared_parts(self, n1, n2, company=False):
        """Parts of the shorter name that match the other name."""
        p1, p2 = self.parts(n1, company), self.parts(n2, company)
        if not p1 or not p2:
            return []
        return self._match(p1, p2)[1]

    def _match(self, p1, p2):
        def matched(src, dst):
            m = 0
            got = []
            used = set()
            i = 0
            while i < len(src):
                hit = False
                for j, d in enumerate(dst):
                    if j not in used and self.part_match(src[i], d):
                        used.add(j); m += 1; hit = True; got.append(src[i]); break
                if not hit and i + 1 < len(src):
                    joined = src[i] + src[i + 1]
                    for j, d in enumerate(dst):
                        if j not in used and self.part_match(joined, d):
                            used.add(j); m += 1; hit = True; got.append(joined); i += 1; break
                if not hit:
                    for j in range(len(dst) - 1):
                        if j not in used and j + 1 not in used and self.part_match(src[i], dst[j] + dst[j + 1]):
                            used.update({j, j + 1}); m += 1; hit = True; got.append(src[i]); break
                i += 1
            return m, got
        short, long_ = (p1, p2) if len(p1) <= len(p2) else (p2, p1)
        m, got = matched(short, long_)
        return (m >= 2 or m == len(short)), got

    def real_parts(self, name, company=False):
        return len(self.parts(name, company))

# ---------------------------------------------------------------- data access
class Data:
    def __init__(self, folder):
        self.alerts = load_jsonl(folder, "alerts.jsonl")
        need = lambda rows, *keys: [r for r in rows if all(r.get(k) for k in keys)]
        self.alerts = need(self.alerts, "alert_id")
        for a in self.alerts:
            a.setdefault("customer_name", "")
            a["customer_name"] = a.get("customer_name") or ""
        self.hits = {h["hit_id"]: h for h in need(load_jsonl(folder, "hits.jsonl"), "hit_id")}
        self.customers = {c["record_id"]: c for c in need(load_jsonl(folder, "customers.jsonl"), "record_id")}
        self.by_email, self.by_phone, self.by_year = {}, {}, {}
        for c in self.customers.values():
            rid = c["record_id"]
            if c.get("email"):
                self.by_email.setdefault(c["email"].lower(), []).append(rid)
            if c.get("phone"):
                self.by_phone.setdefault(c["phone"], []).append(rid)
            d = parse_date(c.get("dob"))
            self.by_year.setdefault(d[0] if d else None, []).append(rid)
        self.acc_by_id, self.acc_by_login = {}, {}
        for a in need(load_jsonl(folder, "accounts.jsonl"), "account_id", "customer_id"):
            self.acc_by_id.setdefault(a["account_id"], []).append(a)
            if a.get("login_handle"):
                self.acc_by_login.setdefault(a["login_handle"].lower(), []).append(a)
        self.applicants_by_cust = {}
        for o in need(load_jsonl(folder, "onboarding.jsonl"), "customer_id", "kyc_applicant_id"):
            self.applicants_by_cust.setdefault(o["customer_id"], []).append(o["kyc_applicant_id"])
        self.kyc_by_app = {}
        for k in need(load_jsonl(folder, "kyc_checks.jsonl"), "applicant_id", "check_id"):
            self.kyc_by_app.setdefault(k["applicant_id"], []).append(k)
        self.notes_by_email = {}
        for n in need(load_jsonl(folder, "notes.jsonl"), "note_id"):
            if n.get("requester_email"):
                self.notes_by_email.setdefault(n["requester_email"].lower(), []).append(n)
        detect_day_first(self.alerts, list(self.customers.values()),
                         [k for ks in self.kyc_by_app.values() for k in ks],
                         [n for ns in self.notes_by_email.values() for n in ns])
        self.search_by_query = {}
        for r in need(load_jsonl(folder, "search_results.jsonl"), "query", "result_id"):
            self.search_by_query.setdefault(r["query"], []).append(r)
        try:
            with open(os.path.join(folder, "name_variants.json"), encoding="utf-8-sig", errors="replace") as fh:
                nv = json.load(fh)
        except (OSError, ValueError):
            nv = {}
            LOAD_WARNINGS.append("name_variants.json: missing or unreadable, using none")
        self.names = Names(normalise_variants(nv))
        # document numbers on approved checks -> records
        self.docs_by_rec, self.recs_by_doc = {}, {}
        for rid in self.customers:
            for k in self.approved_checks(rid):
                dn = (k.get("document_number") or "").strip().upper()
                if dn and not k.get("_from_note"):
                    self.docs_by_rec.setdefault(rid, set()).add(dn)
                    self.recs_by_doc.setdefault(dn, set()).add(rid)

    def approved_checks(self, rid):
        """All approved identity checks for one record: KYC provider records and onboarding-team notes."""
        out = []
        for app in self.applicants_by_cust.get(rid, []):
            for k in self.kyc_by_app.get(app, []):
                if (k.get("result") or "").lower() == "approved":
                    out.append(k)
        c = self.customers[rid]
        for n in self.notes_by_email.get((c.get("email") or "").lower(), []):
            p = parse_kyc_note(n)
            if p:
                out.append(p)
        return out

# ---------------------------------------------------------------- KYC notes
DATE_RE = r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}"
DOC_RE = r"(?:passport|driving licence|national ID|ID card|residence permit)"

def parse_kyc_note(note):
    """An onboarding-team note recording an approved identity check. Returns a check-like dict or None."""
    if (note.get("channel") or "").lower() != "kyc":
        return None
    t = note.get("text") or ""
    if re.search(r"reject|fail|expir|could not|unable|declin|not verified|unsuccess", t, re.I):
        return None
    dates = [d for d in (parse_date(m.group(0)) for m in re.finditer(DATE_RE, t)) if d]
    dob = checked = None
    if len(dates) >= 2:
        srt = sorted(dates)
        dob, checked = srt[0], srt[-1]
    elif len(dates) == 1:
        checked = dates[0]
        m = re.search(r"(?:born|DOB|date of birth(?: of)?)[ :]*(\d{4})\b", t, re.I)
        dob = (int(m.group(1)), None, None) if m else None
    names = re.findall(r"\b[A-Z][A-Z'\-/]+(?:\s+[A-Z][A-Z'\-/]+)+\b", t)
    names = [n for n in names if not re.fullmatch(r"(?:ID|DOB)(?:\s.*)?", n)]
    name = max(names, key=len) if names else None
    country = None
    m = re.search(r"\bfrom (?:the )?([A-Z][A-Za-z'ô\- ]+?)(?=[,.;)]| on | as | and | for | by | showing| matches|$)", t)
    if not m:
        m = re.search(r"\b(?:a|an|the) ([A-Z][A-Za-z'ô\- ]+?) " + DOC_RE, t)
    if not m:
        m = re.search(r"\bissued in (?:the )?([A-Z][A-Za-z'ô\- ]+?)(?=[,.;)]|$)", t)
    if m:
        country = m.group(1).strip()
    return {"check_id": note["note_id"], "verified_name": name, "verified_dob": fmt_date(dob) if dob else None,
            "document_country": country, "checked_at": fmt_date(checked) if checked else "", "result": "approved",
            "document_number": None, "_from_note": True}

# countries mentioned in free-text notes (places the customer lived)
_NOTE_COUNTRY_RE = re.compile(r"\b(" + "|".join(sorted({re.escape(n) for v in COUNTRY.values() for n in v.split("|")
                                                        if len(n) > 3}, key=len, reverse=True)) + r")\b", re.I)

def note_countries(text):
    return {_lookup(m.group(1)) for m in _NAME_SCAN_RE.finditer(fold(text))} - {None, UNKNOWN_COUNTRY}

# ---------------------------------------------------------------- person (customer side)
def merge_person(D, seed):
    """Policy P3: same phone + same full DOB, or same document number on approved checks (transitive)."""
    person, todo = set(), [seed]
    while todo:
        rid = todo.pop()
        if rid in person:
            continue
        person.add(rid)
        c = D.customers[rid]
        d = parse_date(c.get("dob"))
        if d and d[1] and d[2]:
            for o in D.by_phone.get(c.get("phone"), []):
                if o not in person and parse_date(D.customers[o].get("dob")) == d:
                    todo.append(o)
        for dn in D.docs_by_rec.get(rid, ()):
            todo.extend(o for o in D.recs_by_doc.get(dn, ()) if o not in person)
    return sorted(person)

def possible_links(D, recs):
    """Records that share a phone with this person and were NOT merged only because a date of birth is partial or
    unreadable (P3 needs the same full DOB).  They may be the same person, so they block a clearance."""
    out = []
    for rid in recs:
        c = D.customers[rid]
        d = parse_date(c.get("dob"))
        for o in D.by_phone.get(c.get("phone"), []) if c.get("phone") else []:
            if o in recs or o in out:
                continue
            od_raw = D.customers[o].get("dob")
            od = parse_date(od_raw)
            if (not d and c.get("dob")) or (not od and od_raw):
                out.append(o)
            elif d and od and dob_agrees(d, od) and not (d[1] and d[2] and od[1] and od[2]):
                out.append(o)
    return sorted(out)

def person_profile(D, recs, primary):
    p = {"records": recs, "primary": primary, "entity_type": D.customers[primary].get("entity_type"),
         "checks": [], "verified": None, "dobs": [], "countries": {}, "declared_names": [], "country_notes": set(),
         "country_all": {}, "unknown_countries": [],
         "dob_unparsed": []}
    for rid in recs:
        c = D.customers[rid]
        p["checks"].extend(D.approved_checks(rid))
        if parse_date(c.get("dob")):
            p["dobs"].append((parse_date(c["dob"]), rid))
        elif c.get("dob"):
            p["dob_unparsed"].append(f"{c['dob']} ({rid})")
        for f in ("full_name", "preferred_name", "login_name"):
            if c.get(f):
                p["declared_names"].append((c[f], rid))
        for f in ("residence_country", "nationality"):
            cc = country_code(c.get(f))
            if cc == UNKNOWN_COUNTRY:
                p["unknown_countries"].append(f"{c.get(f)} ({rid})")
            elif cc:
                p["countries"].setdefault(cc, rid)
                p["country_all"].setdefault(cc, []).append(rid)
        for n in D.notes_by_email.get((c.get("email") or "").lower(), []):
            if (n.get("channel") or "") != "kyc":
                for cc in note_countries(n.get("text") or ""):
                    p["countries"].setdefault(cc, n["note_id"])
                    p["country_notes"].add(n["note_id"])
                    p["country_all"].setdefault(cc, []).append(n["note_id"])
    for k in p["checks"]:
        if parse_date(k.get("verified_dob")):
            p["dobs"].append((parse_date(k["verified_dob"]), k["check_id"]))
        elif k.get("verified_dob"):
            p["dob_unparsed"].append(f"{k['verified_dob']} ({k['check_id']})")
        cc = country_code(k.get("document_country"))
        if cc == UNKNOWN_COUNTRY:
            p["unknown_countries"].append(f"{k.get('document_country')} ({k['check_id']})")
        elif cc:
            p["countries"].setdefault(cc, k["check_id"])
            p["country_all"].setdefault(cc, []).append(k["check_id"])
    if p["checks"]:
        p["verified"] = max(p["checks"], key=lambda x: (x.get("checked_at") or "", x["check_id"]))
    v = p["verified"]
    if v and parse_date(v.get("verified_dob")):
        p["dob"], p["dob_src"] = parse_date(v["verified_dob"]), v["check_id"]
    else:
        d = parse_date(D.customers[primary].get("dob"))
        p["dob"], p["dob_src"] = (d, primary) if d else (None, None)
    p["verified_names"] = [(k["verified_name"], k["check_id"]) for k in p["checks"] if k.get("verified_name")]
    # names used to agree: the most recent verified name outranks declared names
    p["names"] = [(v["verified_name"], v["check_id"])] if v and v.get("verified_name") else list(p["declared_names"])
    return p

def fits_screened(D, rid, alert):
    """Policy P2: a record fits the screened name, DOB, country and city (each only if screened)."""
    c = D.customers[rid]
    comp = alert.get("entity_type") == "Company"
    if c.get("entity_type") and alert.get("entity_type") and c["entity_type"] != alert["entity_type"]:
        return False
    names = [c.get("full_name"), c.get("preferred_name"), c.get("login_name")]
    if not any(n and D.names.agree(alert["customer_name"], n, comp) for n in names):
        return False
    sd = parse_date(alert.get("dob"))
    if sd:
        d = parse_date(c.get("dob"))
        if (d and not dob_agrees(d, sd)) or (not d and not c.get("dob")):
            return False                   # an unreadable DOB cannot rule a record out (fail-safe)
    sc = country_code(alert.get("country"))
    if sc and sc != UNKNOWN_COUNTRY and sc not in {country_code(c.get("residence_country")), country_code(c.get("nationality"))}:
        return False
    city = fold(alert.get("city") or "")
    if city and city not in fold(c.get("city") or "") and city not in fold(c.get("address") or ""):
        return False
    return True

def resolve_customer(D, alert):
    """Return (list of persons [list of record ids], how)."""
    if alert.get("account_ref"):
        how, cands = "account", [a["customer_id"] for a in D.acc_by_id.get(alert["account_ref"], [])]
    elif alert.get("login_handle"):
        how, cands = "login", [a["customer_id"] for a in D.acc_by_login.get(alert["login_handle"].lower(), [])]
    elif alert.get("email"):
        how, cands = "email", list(D.by_email.get(alert["email"].lower(), []))
    else:
        how = "search"
        sd = parse_date(alert.get("dob"))
        pool = D.by_year.get(sd[0], []) + D.by_year.get(None, []) if sd else D.customers.keys()
        cands = [r for r in pool if fits_screened(D, r, alert)]
    cands = sorted(dict.fromkeys(cands))
    if how != "search" and len(cands) > 1:
        fit = [r for r in cands if fits_screened(D, r, alert)]
        cands = fit or cands
    persons, seen = [], set()
    for r in cands:
        if r in seen:
            continue
        recs = merge_person(D, r)
        seen.update(recs)
        persons.append((r, recs))
    return persons, how

# ---------------------------------------------------------------- hit side
LISTING = {"sanctions": "a sanctioned individual", "pep": "a politically exposed person",
           "enforcement": "the subject of enforcement action", "adverse media": "named in adverse media",
           "soe": "a state-owned enterprise official"}
AGE_RE = re.compile(r"(\d{1,3})[- ]years?[- ](?:old|of age)|\baged? (\d{1,3})\b|\bage of (\d{1,3})\b|\bat age (\d{1,3})\b|"
                    r"\b(?:who|he|she|they) (?:was|were|is|turned) (?:aged )?(\d{1,3})\b|"
                    r"\b(?:was|were|then|at|turned) (\d{2,3})\b(?=[ ,.;]|$)(?! (?:years? (?!old)|percent|million|people|others|counts|of)\b)|, (\d{1,3}),|, (\d{1,3})\b(?= [a-z])|\((?:aged )?(\d{1,3})\)", re.I)
MONTH_YEAR_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                           r"(?: \d{1,2},)? (\d{4})\b")

UNIT_AFTER = re.compile(r"\s*(?:-|\s)?(?:[A-Z]|(?i:pounds?|kilo|kg|grams?|percent|%|million|billion|thousand|people|persons|"
                        r"others|men|women|suspects|militants|passengers|cases|counts|mobile|phones|hand|electric|"
                        r"non-|detonators|safety|snatched|foot|feet|km|miles|years? (?!old)))")

def age_years(text, names=None):
    """'aged 45 in March 2019' -> {1973, 1974}. Returns set of possible birth years or empty.
    Only sentences that name the subject (or use he/she) count, and numbers followed by units are ignored."""
    parts = set()
    for n in names or []:
        parts.update(p for p in re.findall(r"[a-z]+", fold(n)) if len(p) >= 3)
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if re.match(r"\s*According\b", sent):
            continue
        words = set(re.findall(r"[a-z]+", fold(sent)))
        if parts and not (parts & words) and not ({"he", "she"} & words):
            continue
        d = MONTH_YEAR_RE.search(sent)
        if not d:
            continue
        for m in AGE_RE.finditer(sent):
            g = next(x for x in m.groups() if x)
            rest = sent[m.end(m.lastindex):]
            if UNIT_AFTER.match(rest) and not re.match(r"[-\s]*years?[-\s]+(?:old|of age)", rest, re.I):
                continue
            age = int(g)
            if 10 <= age <= 110:
                y = int(d.group(2))
                return {y - age - 1, y - age}
    return set()

DATED_REPORT_RE = re.compile(r"(?m)^According to [^\n;]+; ((?:January|February|March|April|May|June|July|August|"
                             r"September|October|November|December) \d{1,2}, \d{4}):")

def hit_profile(D, h):
    countries, csrc, unknown = {}, {}, []
    for f in ("countries", "nationality", "citizenship", "addresses"):
        for v in as_list(h.get(f)):
            cc = country_code(v, scan=(f == "addresses"))
            if cc == UNKNOWN_COUNTRY:
                unknown.append(v)
                continue
            if cc:
                lab = ("business address" if "[business]" in v.lower() else "address") if f == "addresses" else f
                countries.setdefault(cc, lab)
                csrc.setdefault(cc, set()).add(lab)
    if h.get("place_of_birth"):
        cc = country_code(h["place_of_birth"], scan=True)
        if cc == UNKNOWN_COUNTRY:
            unknown.append(h["place_of_birth"])
        elif cc:
            countries.setdefault(cc, "place_of_birth")
            csrc.setdefault(cc, set()).add("place_of_birth")
    names = [h.get("name")] + [re.sub(r"\(.*?\)", "", a).strip() for a in as_list(h.get("aliases"))]
    names = [n for n in names if n]
    dobs = [d for d in (parse_date(x, "year") for x in as_list(h.get("dobs"))) if d]
    dob_src, dob_ev = ("dobs field", []) if dobs else (None, [])
    notes = h.get("profile_notes") or ""
    if not dobs and h.get("entity_type") != "Company":
        first = notes.strip().split("\n\n")[0].split("\n")[0]
        bm = re.search(r"\bborn (?:on |in )?(" + DATE_RE + r"|\d{4})", first)
        ys = set() if re.match(r"\s*According\b", first) else age_years(first, names)
        if bm and parse_date(bm.group(1), "year"):
            dobs, dob_src = [parse_date(bm.group(1), "year")], "profile notes"
        elif ys:
            dobs, dob_src = [(y, None, None) for y in sorted(ys)], "profile notes"
    if not dobs and h.get("entity_type") != "Company":
        cat = (h.get("primary_category") or (as_list(h.get("categories")) or [""])[0]).lower()
        phrase = LISTING.get(cat, "named on a watchlist")
        ys, ev = set(), []
        seen_q = set()
        for n in names:
            q = " ".join(sorted(re.findall(r"[a-z]+", fold(n))))
            if q in seen_q:
                continue
            seen_q.add(q)
            for r in D.search_by_query.get(q, []):
                sn = r.get("snippet") or ""
                if phrase not in sn:
                    continue
                if not (note_countries(sn) & set(countries)):
                    continue
                y = age_years(sn, names)
                if y:
                    ys |= y
                    ev.append(r["result_id"])
        if ys:
            dobs, dob_src, dob_ev = [(y, None, None) for y in sorted(ys)], "web search results", sorted(set(ev))
    reports = [parse_date(x) for x in DATED_REPORT_RE.findall(notes)]
    reports = [r for r in reports if r]
    business_only = bool(csrc) and all(v == {"business address"} for v in csrc.values())
    return {"names": names, "dobs": dobs, "dob_src": dob_src, "dob_ev": dob_ev, "countries": countries,
            "business_only": business_only, "unknown_countries": unknown,
            "dob_unparsed": [x for x in as_list(h.get("dobs")) if x and not parse_date(x, "year")],
            "deceased": parse_date(h.get("deceased_date"), "year"), "entity_type": h.get("entity_type"),
            "earliest_report": min(reports) if reports else None}


def _surname_parts(D, name, comp):
    """Hit names written 'Surname, Given' -> parts before the comma; otherwise the last part."""
    if "," in name:
        return D.names.parts(name.split(",")[0], comp)
    p = D.names.parts(name, comp)
    return p[-1:] if p else []

# Surnames that are commonly a father's (or grandfather's) given name in Arabic, Hausa, Islamic and West African
# naming traditions: the order of name parts varies between documents, so a "surname-only" overlap is unreliable.
PATRONYMIC_SURNAMES = {
    "muhammad", "mohammed", "mohammad", "mohamed", "muhammed", "mohamad", "mohd", "muhamad", "ahmed", "ahmad", "ahmet",
    "umar", "omar", "omer", "abubakar", "abubakr", "aboubakar", "alhassan", "alhasan", "hassan", "hasan", "hussein",
    "hussain", "husain", "hossain", "hosein", "ibrahim", "ebrahim", "ibraheem", "yusuf", "yousef", "youssef", "yousuf",
    "suleiman", "sulaiman", "sulayman", "suleman", "bello", "garba", "musa", "moussa", "ali", "aly", "abdullah",
    "abdallah", "abdulla", "abdullahi", "usman", "uthman", "othman", "osman", "ismail", "isa", "issa", "idris",
    "adamu", "aliyu", "lawal", "haruna", "yakubu", "yahaya", "yahya", "sani", "danjuma", "shehu", "salisu", "mustapha",
    "mustafa", "abdul", "abdel", "rahman", "rahim", "karim", "khalil", "hamid", "rashid", "said", "saeed", "salah",
    "salim", "saleh", "tijani", "kabir", "nasir", "nuhu", "dauda", "audu", "bashir", "aminu", "amin", "jibril", "zakari",
    "zakariya", "mahmoud", "mahmud", "khalid", "faruk", "farouk", "jamal", "kamal", "malik", "osei", "kofi", "kwame",
}

def is_patronymic_surname(D, part):
    return part in PATRONYMIC_SURNAMES or any(D.names.part_match(part, p) and min(len(part), len(p)) >= 4 and
                                              (D.names.group.get(part, set()) & D.names.group.get(p, set()))
                                              for p in PATRONYMIC_SURNAMES)

def given_names_differ(D, vn, hn, part, comp=False):
    """Ozwald's middle rule. True only when the single shared part is the surname on both sides and both sides
    have full given names that clearly differ (no variant, initial, prefix, joined or edit-distance match).
    Returns (clearly_differ, customer_given, hit_given, why_not)."""
    N = D.names
    raw_tokens = lambda n: [t for t in re.split(r"[^\w]+", fold(re.sub(r"\(.*?\)", " ", n))) if t]
    if any(len(t) == 1 for t in raw_tokens(vn) + raw_tokens(hn)):
        return False, [], [], "one of the names contains initials"
    cs = N.parts(vn, comp)
    if not cs or not N.part_match(cs[-1], part):
        return False, [], [], "the shared part is not the customer's surname (the name may be reordered)"
    if not any(N.part_match(x, part) for x in _surname_parts(D, hn, comp)):
        return False, [], [], "the shared part is not the listed person's surname (the name may be reordered)"
    if not comp and (is_patronymic_surname(D, part) or is_patronymic_surname(D, cs[-1])):
        return False, [], [], (f"the shared surname {part.title()} is commonly a father's given name in Arabic, Hausa "
                               f"and West African naming traditions, so the order of name parts is unreliable")
    cg = [p for p in cs if not N.part_match(p, part)]
    hg = [p for p in N.parts(hn, comp) if not N.part_match(p, part) and p not in _surname_parts(D, hn, comp)]
    if not cg or not hg:
        return False, cg, hg, "one side has no given name to compare"
    full_c = [g for g in cg if len(g) >= 3]
    full_h = [g for g in hg if len(g) >= 3]
    if not full_c or not full_h:
        return False, cg, hg, "the given names are abbreviations only"
    def compatible(a, b):
        if N.variant_link(a, b):
            return True
        if min(len(a), len(b)) <= 2:            # abbreviation such as Md: compatible if the first letter fits
            return a[0] == b[0]
        return a.startswith(b) or b.startswith(a)
    joined_c = cg + [cg[i] + cg[i + 1] for i in range(len(cg) - 1)]
    joined_h = hg + [hg[i] + hg[i + 1] for i in range(len(hg) - 1)]
    for a in joined_c:
        for b in joined_h:
            if compatible(a, b):
                return False, cg, hg, f"the given names {a.title()} and {b.title()} could be variants of each other"
    return True, cg, hg, ""

# ---------------------------------------------------------------- decision helpers
def yrs(dobs):
    return " or ".join(sorted({fmt_date(d) for d in dobs}))

def cnames(codes):
    return ", ".join(sorted(country_name(c) for c in codes))

def certainly_under16(dob, at):
    """Customer was certainly younger than 16 on date `at`, given a possibly partial DOB."""
    y, m, d = dob
    earliest = (y, m or 1, d or 1)               # earliest possible birth date -> oldest possible age
    ay, am, ad = at
    age = ay - earliest[0] - ((am, ad) < (earliest[1], earliest[2]))
    return age < 16

def certainly_after(a, b):
    """Partial date a is certainly after full date b."""
    return date_before(b, a)

def decide_one(D, alert, hit, prof):
    hp = hit_profile(D, hit)
    comp = prof["entity_type"] == "Company"
    recs = prof["records"]
    hname = hit.get("name")
    screened = parse_date(alert.get("screened_at"))
    all_dobs = prof["dobs"]
    R = lambda dec, code, ev, reason: dict(decision=dec, reason_code=code, evidence=list(dict.fromkeys(ev)), reason=reason)

    # Type
    if prof["entity_type"] and hp["entity_type"] and prof["entity_type"] != hp["entity_type"]:
        return R("DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH", recs,
                 f"The customer ({recs[0]}) is {'a company' if comp else 'an individual'} but the hit {hname} is "
                 f"{'a company' if hp['entity_type'] == 'Company' else 'an individual'}. One is a person and the other "
                 f"a company, so they cannot be the same entity.")
    # Time: deceased
    if hp["deceased"] and screened and date_before(hp["deceased"], screened):
        return R("DIFFERENT_ENTITY", "DECEASED", recs,
                 f"The hit's profile records {hname} as deceased on {fmt_date(hp['deceased'])}, before the customer "
                 f"({recs[0]}) was screened on {alert['screened_at']}. A person who died before screening cannot be "
                 f"the live customer.")
    # Time: too young / not yet incorporated at the earliest dated report
    er = hp["earliest_report"]
    if er and all_dobs and not prof["dob_unparsed"]:
        if not comp and all(certainly_under16(d, er) for d, _ in all_dobs):
            return R("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", recs + [prof["dob_src"]],
                     f"The customer was born {fmt_date(prof['dob'] or all_dobs[0][0])} ({prof['dob_src'] or all_dobs[0][1]}), "
                     f"so they were younger than 16 at the hit's earliest dated report on {fmt_date(er)}. Every date of "
                     f"birth the bank holds for the customer gives the same result.")
        if comp and all(certainly_after(d, er) for d, _ in all_dobs):
            return R("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", recs + [prof["dob_src"]],
                     f"The customer company was incorporated {fmt_date(prof['dob'] or all_dobs[0][0])}, after the hit's "
                     f"earliest dated report on {fmt_date(er)}, so it cannot be the company reported on.")
    # DOB contradiction (must hold against every DOB the bank holds)
    cd = prof["dob"]
    if cd and hp["dobs"] and not prof["dob_unparsed"] and not hp["dob_unparsed"] and all(dob_contradicts(x, h) for x, _ in all_dobs for h in hp["dobs"]):
        what = "incorporation date" if comp else "date of birth"
        src = (f" ({hp['dob_src']}: {', '.join(hp['dob_ev'])})" if hp["dob_ev"] else
               (" (from the hit's profile notes)" if hp["dob_src"] == "profile notes" else ""))
        return R("DIFFERENT_ENTITY", "DOB_MISMATCH", [prof["dob_src"]] + hp["dob_ev"],
                 f"The customer's {what} {fmt_date(cd)} ({prof['dob_src']}) contradicts every "
                 f"{'incorporation' if comp else 'birth'} date the hit {hname} gives ({yrs(hp['dobs'])}){src}. "
                 f"No date the bank holds for the customer agrees with the hit's.")
    # agreement facts
    name_ok, name_src, name_hit = False, None, None
    for cn, src in prof["names"]:
        for hn in hp["names"]:
            if D.names.agree(cn, hn, comp):
                name_ok, name_src, name_hit = True, src, hn
                break
        if name_ok:
            break
    enough_parts = comp or any(D.names.real_parts(cn) >= 2 for cn, _ in prof["names"])
    dob_ok = bool(cd and hp["dobs"] and any(dob_agrees(cd, h) for h in hp["dobs"]))
    shared = sorted(c for c in prof["countries"] if c in hp["countries"])
    # Location
    if prof["countries"] and hp["countries"] and not shared and not prof["unknown_countries"] and not hp["unknown_countries"]:
        unparsed = prof["dob_unparsed"] + hp["dob_unparsed"]
        if not comp and name_ok and enough_parts and not dob_ok and unparsed:
            return R("REVIEW", "INSUFFICIENT_DATA", recs + [prof["dob_src"]] + hp["dob_ev"],
                     f"Escalated, not cleared: the customer's name agrees with the hit's name {name_hit}, and the "
                     f"customer is linked to {cnames(prof['countries'])} while the hit is linked to "
                     f"{cnames(hp['countries'])}. A date of birth on file could not be read ({'; '.join(unparsed[:3])}), "
                     f"so a name and date-of-birth match cannot be ruled out and a country difference alone does not "
                     f"clear it. Reviewer: read the date and compare it with the hit's.")
        if not comp and name_ok and enough_parts and dob_ok:
            return R("REVIEW", "INSUFFICIENT_DATA", recs + [prof["dob_src"]] + hp["dob_ev"],
                     f"The customer's name agrees with the hit's name {name_hit} and the date of birth {fmt_date(cd)} "
                     f"({prof['dob_src']}) agrees with the hit's {yrs(hp['dobs'])}. However, the customer is linked to "
                     f"{cnames(prof['countries'])} and the hit to {cnames(hp['countries'])}. Under the policy a country "
                     f"difference does not clear a name and date-of-birth match, so the hit is left for review.")
        # cite the records, the verified identity check and any notes that supplied a country
        ev = recs + ([prof["verified"]["check_id"]] if prof["verified"] else []) + sorted(prof["country_notes"])
        if hp["business_only"] and not comp:
            return R("REVIEW", "INSUFFICIENT_DATA", ev,
                     f"Escalated, not cleared: the customer is linked to {cnames(prof['countries'])}, and the hit "
                     f"{hname}'s only country link is a business address in {cnames(hp['countries'])}. A business "
                     f"address shows where the person works or registered a company, not where they live or their "
                     f"nationality, so it does not support a location clearance. Reviewer: check the listing for "
                     f"nationality, residence and date of birth and compare them with the customer's verified identity.")
        fs = shared_former_state(prof["countries"], hp["countries"])
        if fs:
            name, cc, hc = fs
            return R("REVIEW", "INSUFFICIENT_DATA", ev,
                     f"Escalated, not cleared: the customer is linked to {cnames(cc)} and the hit {hname} to "
                     f"{cnames(hc)}. These countries were part of one state, {name}, so listing data recorded before "
                     f"the split may name either country for the same person. Reviewer: check the dates of the "
                     f"listing's country information, place of birth and nationality before treating this as a "
                     f"location mismatch.")
        return R("DIFFERENT_ENTITY", "LOCATION_MISMATCH", ev,
                 f"The customer is linked only to {cnames(prof['countries'])} (records, identity checks and notes) but "
                 f"the hit {hname} is linked only to {cnames(hp['countries'])}. No country tied to the customer is "
                 f"tied to the hit.")
    # Name: verified legal name agrees with none of the hit's names (all approved checks considered)
    vnames = prof["verified_names"]
    if vnames and not name_ok:
        real = [vn for vn, _ in vnames if comp or D.names.real_parts(vn) >= 2]
        if real and len(real) == len(vnames) and not any(D.names.agree(vn, hn, comp) for vn, _ in vnames for hn in hp["names"]):
            vn, vsrc = prof["names"][0] if prof["verified"] and prof["verified"].get("verified_name") else vnames[0]
            overlaps, doubt = [], None
            for xvn, xsrc in list(dict.fromkeys([(vn, vsrc)] + vnames)):
                for hn in hp["names"]:
                    sp = D.names.shared_parts(xvn, hn, comp)
                    if sp:
                        ok, cg, hg, why = given_names_differ(D, xvn, hn, sp[0], comp) if len(sp) == 1 else (False, [], [], "")
                        overlaps.append((xvn, xsrc, hn, sp[0], cg, hg))
                        if not ok and doubt is None:
                            doubt = (xvn, xsrc, hn, sp[0], why)
            if overlaps and doubt is None:
                xvn, xsrc, hn, part, cg, hg = overlaps[0]
                srcs = sorted({o[1] for o in overlaps})
                return R("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED", srcs,
                         f"The customer's verified legal name {xvn} ({xsrc}) shares only the surname "
                         f"'{part.title()}' with the listed name {hn}. The given names clearly differ: "
                         f"{' '.join(g.title() for g in cg)} versus {' '.join(g.title() for g in hg)}, with no variant, "
                         f"initial or nickname link between them. The verified name therefore does not agree with any "
                         f"of the hit's names or aliases.")
            if overlaps:
                xvn, xsrc, hn, part, why = doubt
                return R("REVIEW", "INSUFFICIENT_DATA", [xsrc],
                         f"Escalated, not cleared: the customer's verified legal name {xvn} ({xsrc}) shares only one "
                         f"name part, '{part.title()}', with the listed name {hn}. Policy needs two matching name parts "
                         f"to treat the names as agreeing, and a single shared name part (usually the surname) is too "
                         f"weak to clear safely{(', and ' + why) if why else ''}. Reviewer: compare the given names, "
                         f"date of birth and nationality against the listing.")
            return R("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED", [vsrc],
                     f"The customer's verified legal name is {vn} ({vsrc}), which does not agree with the hit's name "
                     f"{hname} or any of its aliases. The verified name outranks the name on the customer record.")
    # Confirm
    if not comp and name_ok and enough_parts and dob_ok:
        code = "NAME_DOB_LOCATION_MATCH" if shared else "NAME_DOB_MATCH"
        loc = f" Both the customer and the hit are linked to {cnames(shared)}." if shared else ""
        hsrc = f" ({hp['dob_src']}: {', '.join(hp['dob_ev'])})" if hp["dob_ev"] else (" (hit profile notes)" if hp["dob_src"] == "profile notes" else "")
        return R("SAME_ENTITY", code, recs + [prof["dob_src"]] + ([name_src] if name_src not in recs else []) + hp["dob_ev"],
                 f"The customer's date of birth {fmt_date(cd)} ({prof['dob_src']}) agrees with the hit's "
                 f"{yrs(hp['dobs'])}{hsrc}.{loc} The customer's name agrees with the hit's name {name_hit}, and "
                 f"nothing in the customer's records contradicts the hit.")
    if comp and name_ok and shared:
        src = prof["countries"][shared[0]]
        return R("SAME_ENTITY", "NAME_LOCATION_MATCH", recs + [src] + ([name_src] if name_src not in recs else []),
                 f"Both the customer company and the hit are linked to {cnames(shared)}. The customer's name agrees "
                 f"with the hit's name {name_hit}, and nothing in the customer's records contradicts the hit.")
    # Review
    bits = []
    if shared:
        bits.append(f"Both the customer and the hit are linked to {cnames(shared)}.")
    if name_ok:
        bits.append(f"The customer's name agrees with the hit's name {name_hit}.")
    elif not enough_parts:
        bits.append("The customer's name has fewer than two real name parts, so it can neither confirm nor clear the hit.")
    else:
        bits.append(f"The customer's name does not clearly agree with the hit's name {hname}, but there is no verified legal name that contradicts it.")
    if comp:
        bits.append("There is no shared country and no contradiction, so the company match cannot be settled.")
    elif not hp["dobs"]:
        bits.append("The hit gives no date of birth, so the date of birth cannot be compared; nothing contradicts the hit.")
    elif not cd:
        bits.append("No date of birth was found for the customer; nothing contradicts the hit.")
    else:
        bits.append(f"The customer's date of birth {fmt_date(cd)} neither agrees with nor contradicts every date the hit gives ({yrs(hp['dobs'])}).")
    bits.append("The hit is left for a person to review.")
    vid = [prof["verified"]["check_id"]] if prof["verified"] else []
    if shared:   # cite the records that tie the customer to the shared country
        ev = sorted({x for c in shared for x in prof["country_all"].get(c, [])})
    else:
        ev = recs + vid
    return R("REVIEW", "INSUFFICIENT_DATA", ev + ([prof["dob_src"]] if cd and hp["dobs"] else []), " ".join(bits[:4]))

def decide(D, alert, hit, persons, how):
    base = {"hit_id": hit["hit_id"], "alert_id": alert["alert_id"]}
    if not persons:
        return {**base, "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None,
                "reason": ("No customer record in the system of record fits the screened name, date of birth and country"
                           if how == "search" else f"The alert's {how} link did not lead to any customer record") +
                          ", so the hit cannot be compared and is left for review.", "evidence": []}
    results = []
    for primary, recs in persons:
        prof = person_profile(D, recs, primary)
        results.append((primary, recs, decide_one(D, alert, hit, prof)))
    if len(results) == 1:
        primary, recs, res = results[0]
        return {**base, "decision": res["decision"], "reason_code": res["reason_code"], "customer_record_id": primary,
                "reason": res["reason"], "evidence": [e for e in res["evidence"] if e]}
    decisions = {r["decision"] for _, _, r in results}
    allrecs = [p for p, _, _ in results]
    if len(decisions) == 1:
        _, _, res = results[0]
        ev = list(dict.fromkeys(e for _, _, r in results for e in r["evidence"] if e))
        return {**base, "decision": res["decision"], "reason_code": res["reason_code"], "customer_record_id": "AMBIGUOUS",
                "reason": f"{len(results)} different people fit the alert ({', '.join(allrecs)}) and each leads to the "
                          f"same decision. " + res["reason"], "evidence": ev}
    detail = "; ".join(f"{p}: {r['decision']} ({r['reason_code']})" for p, _, r in results)
    return {**base, "decision": "REVIEW", "reason_code": "AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id": "AMBIGUOUS",
            "reason": f"{len(results)} different people in the system of record fit the alert and they lead to different "
                      f"decisions ({detail}). The hit is left for a person to review.",
            "evidence": allrecs}


# ---------------------------------------------------------------- independent second check of clearances
def _raw_customer(D, recs):
    """Re-collect the person's identity facts straight from the raw tables (not from person_profile)."""
    dobs, countries, unknown, types, vnames, unparsed = [], set(), False, set(), [], False
    for rid in recs:
        c = D.customers.get(rid) or {}
        types.add(c.get("entity_type"))
        d = parse_date(c.get("dob"))
        if d:
            dobs.append(d)
        unparsed |= bool(c.get("dob")) and not d
        for f in ("residence_country", "nationality"):
            cc = country_code(c.get(f))
            unknown |= cc == UNKNOWN_COUNTRY
            if cc and cc != UNKNOWN_COUNTRY:
                countries.add(cc)
        checks = [k for app in D.applicants_by_cust.get(rid, []) for k in D.kyc_by_app.get(app, [])
                  if str(k.get("result") or "").strip().lower() == "approved"]
        for n in D.notes_by_email.get(str(c.get("email") or "").lower(), []):
            p = parse_kyc_note(n)
            if p:
                checks.append(p)
            elif (n.get("channel") or "") != "kyc":
                countries |= note_countries(n.get("text") or "")
        for k in checks:
            d = parse_date(k.get("verified_dob"))
            if d:
                dobs.append(d)
            unparsed |= bool(k.get("verified_dob")) and not d
            cc = country_code(k.get("document_country"))
            unknown |= cc == UNKNOWN_COUNTRY
            if cc and cc != UNKNOWN_COUNTRY:
                countries.add(cc)
            if k.get("verified_name"):
                vnames.append(k["verified_name"])
    return {"dobs": dobs, "countries": countries, "unknown": unknown, "types": types - {None}, "vnames": vnames,
            "unparsed": unparsed}

def _raw_hit_countries(h):
    cs, unknown, tags = set(), False, []
    for f in ("countries", "nationality", "citizenship", "addresses", "place_of_birth"):
        for v in as_list(h.get(f)) if f != "place_of_birth" else ([h[f]] if h.get(f) else []):
            cc = country_code(v, scan=f in ("addresses", "place_of_birth"))
            if cc == UNKNOWN_COUNTRY:
                unknown = True
            elif cc:
                cs.add(cc)
                tags.append("business" if f == "addresses" and "[business]" in str(v).lower() else f)
    return cs, unknown, bool(tags) and all(t == "business" for t in tags)

def _contradicts(c, h):
    if abs(c[0] - h[0]) >= 2:
        return True
    return c[0] == h[0] and bool(c[1] and h[1]) and (c[1] != h[1] or bool(c[2] and h[2] and c[2] != h[2]))

def _verify_one(D, alert, hit, recs, code):
    cust = _raw_customer(D, recs)
    comp = "Company" in cust["types"]
    if code == "ENTITY_TYPE_MISMATCH":
        return bool(cust["types"]) and hit.get("entity_type") not in cust["types"] and len(cust["types"]) == 1
    if code == "DECEASED":
        dd, sd = parse_date(hit.get("deceased_date"), "year"), parse_date(alert.get("screened_at"))
        return bool(dd and sd and date_before(dd, sd))
    hp = hit_profile(D, hit)
    if code == "TEMPORAL_IMPOSSIBILITY":
        reps = [parse_date(x) for x in re.findall(r"(?m)^According to [^\n;]+; ([A-Z][a-z]+ \d{1,2}, \d{4}):",
                                                   hit.get("profile_notes") or "")]
        reps = [r for r in reps if r]
        if not reps or not cust["dobs"] or cust["unparsed"]:
            return False
        er = min(reps)
        if comp:
            return all(date_before(er, d) for d in cust["dobs"])
        return all((er[0] - d[0]) - ((er[1], er[2]) < ((d[1] or 1), (d[2] or 1))) < 16 for d in cust["dobs"])
    if code == "DOB_MISMATCH":
        hit_unparsed = any(x and not parse_date(x, "year") for x in as_list(hit.get("dobs")))
        return bool(cust["dobs"] and hp["dobs"]) and not cust["unparsed"] and not hit_unparsed and all(_contradicts(c, h) for c in cust["dobs"] for h in hp["dobs"])
    if code == "LOCATION_MISMATCH":
        hcs, hunk, business_only = _raw_hit_countries(hit)
        if not cust["countries"] or not hcs or cust["unknown"] or hunk or (cust["countries"] & hcs):
            return False
        if shared_former_state(cust["countries"], hcs) or (business_only and not comp):
            return False
        names = cust["vnames"] or [D.customers[r].get("full_name") for r in recs if D.customers.get(r)]
        name_ok = any(n and D.names.agree(n, hn, comp) for n in names for hn in hp["names"])
        dob_ok = any(c[0] == h[0] and not _contradicts(c, h) for c in cust["dobs"] for h in hp["dobs"])
        hit_unparsed = any(x and not parse_date(x, "year") for x in as_list(hit.get("dobs")))
        return not (name_ok and (dob_ok or cust["unparsed"] or hit_unparsed) and not comp)
    if code == "NAME_MISMATCH_VERIFIED":
        if not cust["vnames"]:
            return False
        for vn in cust["vnames"]:
            if not comp and D.names.real_parts(vn) < 2:
                return False
            for hn in hp["names"]:
                if D.names.agree(vn, hn, comp):
                    return False
                sp = D.names.shared_parts(vn, hn, comp)
                if sp and not (len(sp) == 1 and given_names_differ(D, vn, hn, sp[0], comp)[0]):
                    return False
        return True
    return False

def second_check(D, alert, hit, persons, r):
    """Independently re-derive a clearance from the raw records; if it cannot be reproduced, downgrade to REVIEW."""
    code = r["reason_code"]
    targets = [recs for primary, recs in persons] if r["customer_record_id"] == "AMBIGUOUS" else \
              [recs for primary, recs in persons if primary == r["customer_record_id"]]
    ok = bool(targets)
    links = sorted({o for recs in targets for o in possible_links(D, recs)})
    if links:
        out = dict(r)
        out.update(decision="REVIEW", reason_code="INSUFFICIENT_DATA",
                   evidence=list(dict.fromkeys(r["evidence"] + links)),
                   reason=f"Escalated, not cleared: the first pass found a {code.replace('_', ' ').lower()} for "
                          f"{hit.get('name')}, but record(s) {', '.join(links)} share the customer's phone number and "
                          f"have a partial or unreadable date of birth, so they may be the same person and were not "
                          f"checked against the hit. Reviewer: confirm whether those records belong to the customer and "
                          f"compare their details with the hit.")
        return out
    for recs in targets:
        try:
            ok = ok and _verify_one(D, alert, hit, recs, code)
        except Exception:
            ok = False
    if ok:
        return r
    out = dict(r)
    out.update(decision="REVIEW", reason_code="INSUFFICIENT_DATA",
               reason=f"Escalated, not cleared: the first pass found a {code.replace('_', ' ').lower()} for "
                      f"{hit.get('name')}, but an independent second check of the raw records could not reproduce "
                      f"that contradiction, so the hit is not cleared. Reviewer: check the customer's verified identity "
                      f"against the listing before deciding. First-pass reason: {r['reason']}")
    return out

def error_review(hit_id, alert_id, what):
    return {"hit_id": hit_id, "alert_id": alert_id, "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA",
            "customer_record_id": None,
            "reason": f"Escalated, not cleared: the automated review could not complete for this hit ({what}). "
                      f"Reviewer: assess the hit manually against the customer's records.", "evidence": []}

def main(folder, outfile):
    try:
        D = Data(folder)
    except Exception as e:                         # cannot even load: still emit a REVIEW line per readable hit
        D = None
        LOAD_WARNINGS.append(f"data load failed: {type(e).__name__}: {e}")
    out, done = [], set()
    alerts = sorted([a for a in (D.alerts if D else []) if a.get("alert_id")], key=lambda a: str(a["alert_id"]))
    hits = D.hits if D else {h["hit_id"]: h for h in load_jsonl(folder, "hits.jsonl") if h.get("hit_id")}
    by_alert = {}
    for h in hits.values():
        by_alert.setdefault(h.get("alert_id"), []).append(h["hit_id"])
    for alert in alerts:
        ids = list(dict.fromkeys(as_list(alert.get("hit_ids")) + sorted(by_alert.get(alert["alert_id"], []))))
        try:
            persons, how = resolve_customer(D, alert)
            err = None
        except Exception as e:
            persons, how, err = [], "error", f"customer lookup failed: {type(e).__name__}"
        for hid in ids:
            hit = hits.get(hid)
            if hit is None or hid in done:
                continue
            done.add(hid)
            if err:
                out.append(error_review(hid, alert["alert_id"], err))
                continue
            try:
                r = decide(D, alert, hit, persons, how)
                if r["decision"] == "DIFFERENT_ENTITY":
                    r = second_check(D, alert, hit, persons, r)
                out.append(r)
            except Exception as e:
                out.append(error_review(hid, alert["alert_id"], f"{type(e).__name__} while deciding"))
    for hid in sorted(hits):                       # hits whose alert is missing still get a line
        if hid not in done:
            out.append(error_review(hid, hits[hid].get("alert_id"), "its alert was not found"))
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    with open(outfile, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    for w in LOAD_WARNINGS[:20]:
        print("warning:", w, file=sys.stderr)
    print(f"wrote {len(out)} decisions to {outfile}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run <data folder> <output file>")
    main(sys.argv[1], sys.argv[2])
