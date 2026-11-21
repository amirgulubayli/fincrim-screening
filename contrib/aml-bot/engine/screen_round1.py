#!/usr/bin/env python3
"""Track 2 identity screening, round 1. Deterministic, standard library only."""
import json, os, re, sys, unicodedata
from datetime import date

# ---------------------------------------------------------------- loading
def load_jsonl(folder, name):
    p = os.path.join(folder, name)
    out = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
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

def parse_date(s):
    """Return (year, month|None, day|None) or None. Keeps partial dates partial."""
    if s is None:
        return None
    s = str(s).strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", s)
    if m:
        return (int(m[1]), int(m[2]) if m[2] else None, int(m[3]) if m[3] else None)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)          # dd/mm/yyyy
    if m:
        return (int(m[3]), int(m[2]), int(m[1]))
    m = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", s)     # Month d, yyyy
    if m and m[1].lower() in MONTHS:
        return (int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+)\.? (\d{4})", s)       # d Month yyyy
    if m and m[2].lower() in MONTHS:
        return (int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    m = re.fullmatch(r"([A-Za-z]+) (\d{4})", s)                    # Month yyyy
    if m and m[1].lower() in MONTHS:
        return (int(m[2]), MONTHS[m[1].lower()], None)
    return None

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
}
_CMAP = {}
for code, names in COUNTRY.items():
    _CMAP[code.lower()] = code
    for n in names.split("|"):
        _CMAP[n] = code
NOT_COUNTRIES = {"international", "europe", "n/a", "unknown", ""}

def fold(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()

def country_code(s):
    if s is None:
        return None
    s = re.sub(r"\[.*?\]", "", str(s)).strip()
    k = fold(s.split(",")[-1])
    if k in NOT_COUNTRIES:
        return None
    return _CMAP.get(k, k.upper())   # unknown names kept as their own token

def country_name(code):
    n = COUNTRY.get(code)
    return n.split("|")[0].title() if n else code

# ---------------------------------------------------------------- names
PLACEHOLDERS = {"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbc", "tba"}

class Names:
    def __init__(self, variants):
        self.group = {}
        for gi, g in enumerate(variants.get("given_name_groups", [])):
            for n in g:
                self.group.setdefault(fold(n), set()).add(gi)
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

    def agree(self, n1, n2, company=False):
        p1, p2 = self.parts(n1, company), self.parts(n2, company)
        if not p1 or not p2:
            return False
        # parts written together: try joining adjacent parts on each side
        def matched(src, dst):
            m = 0
            used = set()
            i = 0
            while i < len(src):
                hit = False
                for j, d in enumerate(dst):
                    if j not in used and self.part_match(src[i], d):
                        used.add(j); m += 1; hit = True; break
                if not hit and i + 1 < len(src):
                    joined = src[i] + src[i + 1]
                    for j, d in enumerate(dst):
                        if j not in used and self.part_match(joined, d):
                            used.add(j); m += 1; hit = True; i += 1; break
                if not hit:
                    for j in range(len(dst) - 1):
                        if j not in used and j + 1 not in used and self.part_match(src[i], dst[j] + dst[j + 1]):
                            used.update({j, j + 1}); m += 1; hit = True; break
                i += 1
            return m
        short, long_ = (p1, p2) if len(p1) <= len(p2) else (p2, p1)
        m = matched(short, long_)
        return m >= 2 or m == len(short)

    def real_parts(self, name, company=False):
        return len(self.parts(name, company))

# ---------------------------------------------------------------- data access
class Data:
    def __init__(self, folder):
        self.folder = folder
        self.alerts = load_jsonl(folder, "alerts.jsonl")
        self.hits = {h["hit_id"]: h for h in load_jsonl(folder, "hits.jsonl")}
        self.customers = {c["record_id"]: c for c in load_jsonl(folder, "customers.jsonl")}
        self.by_email = {}
        for c in self.customers.values():
            if c.get("email"):
                self.by_email.setdefault(c["email"].lower(), []).append(c["record_id"])
        self.acc_by_id, self.acc_by_login, self.acc_by_cust = {}, {}, {}
        for a in load_jsonl(folder, "accounts.jsonl"):
            self.acc_by_id.setdefault(a["account_id"], []).append(a)
            if a.get("login_handle"):
                self.acc_by_login.setdefault(a["login_handle"].lower(), []).append(a)
            self.acc_by_cust.setdefault(a["customer_id"], []).append(a)
        self.applicants_by_cust = {}
        for o in load_jsonl(folder, "onboarding.jsonl"):
            self.applicants_by_cust.setdefault(o["customer_id"], []).append(o["kyc_applicant_id"])
        self.kyc_by_app = {}
        for k in load_jsonl(folder, "kyc_checks.jsonl"):
            self.kyc_by_app.setdefault(k["applicant_id"], []).append(k)
        self.notes_by_email = {}
        for n in load_jsonl(folder, "notes.jsonl"):
            if n.get("requester_email"):
                self.notes_by_email.setdefault(n["requester_email"].lower(), []).append(n)
        with open(os.path.join(folder, "name_variants.json"), encoding="utf-8") as fh:
            self.names = Names(json.load(fh))

    def latest_approved_kyc(self, rid):
        best = None
        for app in self.applicants_by_cust.get(rid, []):
            for k in self.kyc_by_app.get(app, []):
                if (k.get("result") or "").lower() != "approved":
                    continue
                if best is None or (k.get("checked_at") or "") > (best.get("checked_at") or ""):
                    best = k
        return best

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
    dates = [(m.group(0), parse_date(m.group(0))) for m in re.finditer(DATE_RE, t)]
    dates = [(raw, d) for raw, d in dates if d]
    dob = checked = None
    if len(dates) >= 2:
        srt = sorted(dates, key=lambda x: x[1])
        dob, checked = srt[0][1], srt[-1][1]
    elif len(dates) == 1:
        checked = dates[0][1]
        m = re.search(r"(?:born|DOB|date of birth(?: of)?)[ :]*(\d{4})\b", t, re.I)
        dob = (int(m.group(1)), None, None) if m else None
    names = re.findall(r"\b[A-Z][A-Z'\-/]+(?:\s+[A-Z][A-Z'\-/]+)+\b", t)
    names = [n for n in names if not re.fullmatch(r"(?:ID|DOB)(?:\s.*)?", n)]
    name = max(names, key=len) if names else None
    country = None
    m = re.search(r"\bfrom (?:the )?([A-Z][A-Za-z'ô\- ]+?)(?=[,.;)]| on | as | and | for | by | showing| matches|$)", t)
    if not m:
        m = re.search(r"\b(?:a|an|the) ([A-Z][A-Za-z'ô\- ]+?) " + DOC_RE, t)
    if m:
        country = m.group(1).strip()
    return {"check_id": note["note_id"], "verified_name": name, "verified_dob": fmt_date(dob) if dob else None,
            "document_country": country, "checked_at": fmt_date(checked) if checked else "", "result": "approved",
            "_from_note": True}

# ---------------------------------------------------------------- customer side
def verified_check(D, rid):
    """Most recent approved identity check: KYC provider record or onboarding-team note."""
    c = D.customers[rid]
    cands = []
    k = D.latest_approved_kyc(rid)
    if k:
        cands.append(k)
    for n in D.notes_by_email.get((c.get("email") or "").lower(), []):
        p = parse_kyc_note(n)
        if p:
            cands.append(p)
    if not cands:
        return None
    return max(cands, key=lambda x: (x.get("checked_at") or "", x["check_id"]))

def all_customer_dobs(D, rid):
    """Every DOB any source gives for the customer, with its source ID (used as a safety rail)."""
    c = D.customers[rid]
    out = []
    if parse_date(c.get("dob")):
        out.append((parse_date(c["dob"]), rid))
    for app in D.applicants_by_cust.get(rid, []):
        for k in D.kyc_by_app.get(app, []):
            if (k.get("result") or "").lower() == "approved" and parse_date(k.get("verified_dob")):
                out.append((parse_date(k["verified_dob"]), k["check_id"]))
    for n in D.notes_by_email.get((c.get("email") or "").lower(), []):
        p = parse_kyc_note(n)
        if p and parse_date(p.get("verified_dob")):
            out.append((parse_date(p["verified_dob"]), p["check_id"]))
    return out

def customer_profile(D, rid):
    c = D.customers[rid]
    kyc = verified_check(D, rid)
    prof = {"record_ids": [rid], "entity_type": c.get("entity_type"), "kyc": kyc,
            "names": [], "dob": None, "dob_src": rid, "countries": {}, "verified_name": None}
    if kyc and kyc.get("verified_name"):
        prof["verified_name"] = kyc["verified_name"]
        prof["names"].append((kyc["verified_name"], kyc["check_id"]))
    prof["declared_names"] = [(c[f], rid) for f in ("full_name", "preferred_name", "login_name") if c.get(f)]
    if not prof["names"]:            # no verified name: fall back to declared names
        prof["names"] = list(prof["declared_names"])
    prof["all_dobs"] = all_customer_dobs(D, rid)
    if kyc and parse_date(kyc.get("verified_dob")):
        prof["dob"], prof["dob_src"] = parse_date(kyc["verified_dob"]), kyc["check_id"]
    elif parse_date(c.get("dob")):
        prof["dob"] = parse_date(c["dob"])
    for f in ("residence_country", "nationality"):
        cc = country_code(c.get(f))
        if cc:
            prof["countries"].setdefault(cc, rid)
    if kyc:
        cc = country_code(kyc.get("document_country"))
        if cc:
            prof["countries"].setdefault(cc, kyc["check_id"])
    return prof

def fits_screened(D, rid, alert):
    """Does this record fit the screened name and DOB (used to pick a joint holder)?"""
    c = D.customers[rid]
    comp = (alert.get("entity_type") == "Company")
    kyc = verified_check(D, rid)
    names = [c.get("full_name"), c.get("preferred_name"), kyc.get("verified_name") if kyc else None]
    if not any(n and D.names.agree(alert["customer_name"], n, comp) for n in names):
        return False
    sd = parse_date(alert.get("dob"))
    if sd:
        dobs = [parse_date(c.get("dob"))] + ([parse_date(kyc.get("verified_dob"))] if kyc else [])
        dobs = [d for d in dobs if d]
        if dobs and not any(dob_agrees(d, sd) or dob_agrees(sd, d) for d in dobs):
            return False
    return True

def resolve_customer(D, alert):
    """Return (list of candidate record ids, how). Round 1: linked alerts only."""
    cands, how = [], None
    if alert.get("account_ref"):
        how = "account"
        cands = [a["customer_id"] for a in D.acc_by_id.get(alert["account_ref"], [])]
    elif alert.get("login_handle"):
        how = "login"
        cands = [a["customer_id"] for a in D.acc_by_login.get(alert["login_handle"].lower(), [])]
    elif alert.get("email"):
        how = "email"
        cands = list(D.by_email.get(alert["email"].lower(), []))
    else:
        return [], "unlinked"
    cands = list(dict.fromkeys(cands))
    if len(cands) > 1:
        fit = [r for r in cands if fits_screened(D, r, alert)]
        cands = fit if fit else cands
    return cands, how

# ---------------------------------------------------------------- hit side
def hit_profile(h):
    countries = {}
    for f in ("countries", "nationality", "citizenship", "addresses", "place_of_birth"):
        vals = as_list(h.get(f)) if f != "place_of_birth" else ([h.get(f)] if h.get(f) else [])
        for v in vals:
            cc = country_code(v)
            if cc:
                countries.setdefault(cc, f)
    names = [h.get("name")] + [re.sub(r"\((?:AKA|FKA|NKA|Low Quality AKA|.*?)\)", "", a).strip()
                               for a in as_list(h.get("aliases"))]
    dobs = [d for d in (parse_date(x) for x in as_list(h.get("dobs"))) if d]
    return {"names": [n for n in names if n], "dobs": dobs, "countries": countries,
            "deceased": parse_date(h.get("deceased_date")), "entity_type": h.get("entity_type")}

# ---------------------------------------------------------------- decision
def yrs(dobs):
    return " or ".join(sorted({fmt_date(d) for d in dobs}))

def decide_one(D, alert, hit, rid):
    """Decide one hit against one resolved customer record. Returns dict."""
    cp = customer_profile(D, rid)
    hp = hit_profile(hit)
    comp = cp["entity_type"] == "Company"
    ev = [rid]
    hname = hit.get("name")
    screened = parse_date(alert.get("screened_at"))

    # 1 Type
    if cp["entity_type"] and hp["entity_type"] and cp["entity_type"] != hp["entity_type"]:
        return dict(decision="DIFFERENT_ENTITY", reason_code="ENTITY_TYPE_MISMATCH", evidence=ev,
                    reason=f"The customer ({rid}) is {'a company' if comp else 'an individual'} but the hit {hname} "
                           f"is {'a company' if hp['entity_type']=='Company' else 'an individual'}. "
                           f"One is a person and the other a company, so they cannot be the same entity.")
    # 2 Deceased before screening
    if hp["deceased"] and screened and date_before(hp["deceased"], screened):
        return dict(decision="DIFFERENT_ENTITY", reason_code="DECEASED", evidence=ev,
                    reason=f"The hit's profile records {hname} as deceased on {fmt_date(hp['deceased'])}, "
                           f"before the customer ({rid}) was screened on {alert['screened_at']}. "
                           f"A person who died before screening cannot be the live customer.")
    # name agreement
    name_ok, name_src, name_hit = False, None, None
    for cn, src in cp["names"]:
        for hn in hp["names"]:
            if D.names.agree(cn, hn, comp):
                name_ok, name_src, name_hit = True, src, hn
                break
        if name_ok:
            break
    enough_parts = comp or any(D.names.real_parts(cn) >= 2 for cn, _ in cp["names"])
    # 3 DOB contradiction (needs a customer DOB and at least one hit DOB)
    cd = cp["dob"]
    every = [d for d, _ in cp["all_dobs"]] + ([cd] if cd else [])
    if cd and hp["dobs"] and all(dob_contradicts(x, h) for x in every for h in hp["dobs"]):
        e = list(dict.fromkeys(ev + [cp["dob_src"]]))
        what = "incorporation date" if comp else "date of birth"
        return dict(decision="DIFFERENT_ENTITY", reason_code="DOB_MISMATCH", evidence=e,
                    reason=f"The customer's {what} {fmt_date(cd)} ({cp['dob_src']}) contradicts every "
                           f"{'incorporation' if comp else 'birth'} date the hit {hname} gives ({yrs(hp['dobs'])}). "
                           f"No date given for the hit agrees with the customer's.")
    # country agreement
    shared = [c for c in cp["countries"] if c in hp["countries"]]
    # 4 Confirm
    dob_ok = bool(cd and hp["dobs"] and any(dob_agrees(cd, h) for h in hp["dobs"]))
    loc_conflict = bool(cp["countries"]) and bool(hp["countries"]) and not shared
    if not comp and name_ok and enough_parts and dob_ok and loc_conflict:
        e = list(dict.fromkeys(ev + [cp["dob_src"]]))
        return dict(decision="REVIEW", reason_code="INSUFFICIENT_DATA", evidence=e,
                    reason=f"The customer's name agrees with the hit's name {name_hit} and the date of birth {fmt_date(cd)} "
                           f"({cp['dob_src']}) agrees with the hit's {yrs(hp['dobs'])}. However, the customer is linked to "
                           f"{', '.join(country_name(c) for c in cp['countries'])} and the hit to "
                           f"{', '.join(country_name(c) for c in hp['countries'])}. Under the policy a country difference "
                           f"does not clear a name and date-of-birth match, so the hit is left for review.")
    if not comp and name_ok and enough_parts and dob_ok:
        e = list(dict.fromkeys(ev + [cp["dob_src"]] + ([name_src] if name_src != rid else [])))
        code = "NAME_DOB_LOCATION_MATCH" if shared else "NAME_DOB_MATCH"
        loc = f" Both the customer and the hit are linked to {country_name(shared[0])}." if shared else ""
        return dict(decision="SAME_ENTITY", reason_code=code, evidence=e,
                    reason=f"The customer's date of birth {fmt_date(cd)} ({cp['dob_src']}) agrees with the hit's "
                           f"{yrs(hp['dobs'])}.{loc} The customer's name agrees with the hit's name {name_hit}, "
                           f"and nothing in the customer's records contradicts the hit.")
    if comp and name_ok and shared:
        src = cp["countries"][shared[0]]
        e = list(dict.fromkeys(ev + [src] + ([name_src] if name_src != rid else [])))
        return dict(decision="SAME_ENTITY", reason_code="NAME_LOCATION_MATCH", evidence=e,
                    reason=f"Both the customer company and the hit are linked to {country_name(shared[0])}. "
                           f"The customer's name agrees with the hit's name {name_hit}, and nothing in the "
                           f"customer's records contradicts the hit.")
    # 5 Review
    bits = []
    if name_ok:
        bits.append(f"The customer's name agrees with the hit's name {name_hit}.")
    elif not enough_parts:
        bits.append("The customer's name has fewer than two real name parts, so it can neither confirm nor clear the hit.")
    elif cp["verified_name"] and any(D.names.agree(cn, hn, comp) for cn, _ in cp["declared_names"] for hn in hp["names"]):
        bits.append(f"The name on the customer record agrees with the hit's name {hname}, but the verified legal name "
                    f"{cp['verified_name']} ({cp['kyc']['check_id']}) does not; round 1 does not clear on this yet.")
    else:
        bits.append(f"The customer's name was not confirmed to agree with the hit's name {hname}, but no verified-name contradiction was established.")
    if not comp:
        if not hp["dobs"]:
            bits.append("The hit gives no date of birth, so the date of birth cannot be compared.")
        elif not cd:
            bits.append("No date of birth was found for the customer.")
        else:
            bits.append(f"The customer's date of birth {fmt_date(cd)} neither clearly agrees with nor contradicts the hit's {yrs(hp['dobs'])}.")
    else:
        bits.append("No shared country was found to confirm the company match.")
    bits.append("Nothing found settles it, so the hit is left for a person to review.")
    e = list(dict.fromkeys(ev + ([cp["dob_src"]] if cd and cp["dob_src"] != rid else [])))
    return dict(decision="REVIEW", reason_code="INSUFFICIENT_DATA", evidence=e, reason=" ".join(bits[:4]))

def decide(D, alert, hit, cands, how):
    base = {"hit_id": hit["hit_id"], "alert_id": alert["alert_id"]}
    if how == "unlinked":
        return {**base, "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None,
                "reason": "The alert carries no account, login or email link to a customer record. Round 1 does not yet "
                          "search the system of record for unlinked alerts, so the customer was not resolved and the "
                          "hit is left for review.", "evidence": []}
    if not cands:
        return {**base, "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA", "customer_record_id": None,
                "reason": f"The alert's {how} link did not lead to any customer record, so the hit cannot be "
                          f"compared and is left for review.", "evidence": []}
    results = [(r, decide_one(D, alert, hit, r)) for r in cands]
    if len(results) == 1:
        r, res = results[0]
        return {**base, "decision": res["decision"], "reason_code": res["reason_code"],
                "customer_record_id": r, "reason": res["reason"], "evidence": res["evidence"]}
    decisions = {res["decision"] for _, res in results}
    if len(decisions) == 1:
        r, res = results[0]
        ev = list(dict.fromkeys([x for _, rr in results for x in rr["evidence"]]))
        return {**base, "decision": res["decision"], "reason_code": res["reason_code"],
                "customer_record_id": "AMBIGUOUS", "reason": res["reason"] +
                f" More than one record fits the alert ({', '.join(cands)}) and all lead to this decision.",
                "evidence": ev}
    return {**base, "decision": "REVIEW", "reason_code": "AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id": "AMBIGUOUS",
            "reason": f"More than one customer record fits the alert ({', '.join(cands)}) and they lead to different "
                      f"decisions ({', '.join(sorted(decisions))}). The hit is left for review.",
            "evidence": list(cands)}

def main(folder, outfile):
    D = Data(folder)
    out = []
    for alert in sorted(D.alerts, key=lambda a: a["alert_id"]):
        cands, how = resolve_customer(D, alert)
        for hid in as_list(alert.get("hit_ids")):
            hit = D.hits.get(hid)
            if hit is None:
                continue
            out.append(decide(D, alert, hit, cands, how))
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    with open(outfile, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(out)} decisions to {outfile}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run <data folder> <output file>")
    main(sys.argv[1], sys.argv[2])
