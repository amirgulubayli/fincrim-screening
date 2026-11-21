"""Format-tolerant input loader (standard library only).

The official input schema is not published, so this module accepts the data in whatever reasonable shape it
arrives and maps it onto the field names the rules use (the brief's file list and the development set's columns).

* Files: JSON Lines, a JSON array (or {"data": [...]}), a JSON object holding several tables, CSV/TSV, or an
  .xlsx workbook (one table per file, or one sheet per table). Found by name anywhere up to two folders down
  (alerts.jsonl, Alerts.csv, dev_alerts.json, a "KYC checks" sheet, ...).
* Fields: case, spaces, hyphens and camelCase are ignored; common synonyms are mapped (date_of_birth, birthDate ->
  dob; nationality/country; type -> entity_type; ...); nested objects are flattened; lists or "; " strings both work.
* Alerts may carry their hits nested inside them, or hits may arrive as separate rows that only carry an alert id
  (alert rows are then rebuilt from the hit rows).
* Anything that cannot be mapped is reported on stderr and handled fail-safe: if a table or a field the clearance
  rules depend on cannot be found, `DEGRADED` is set and the engine clears nothing (every clearance becomes REVIEW).
"""
import csv, io, json, os, re, zipfile
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

LOAD_WARNINGS = []          # human-readable warnings, printed on stderr by the engine
DEGRADED = []               # reasons the input is not fully understood; non-empty => no clearances
UNMAPPED = {}               # table -> {field: rows}
NESTED_HITS = []            # hit rows found inside alert rows

TABLES = ["alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"]
TABLE_STEMS = {
    "alerts": ["alerts", "alert", "screening_alerts", "alert_list"],
    "hits": ["hits", "hit", "matches", "match", "watchlist_hits", "list_hits", "profiles", "watchlist"],
    "customers": ["customers", "customer", "system_of_record", "customer_records", "clients", "client"],
    "accounts": ["accounts", "account"],
    "onboarding": ["onboarding", "onboardings", "applications", "onboarding_records"],
    "kyc_checks": ["kyc_checks", "kyc_check", "kyc", "kycchecks", "identity_checks", "idv_checks", "kyc_results"],
    "notes": ["notes", "note", "support_notes", "case_notes"],
    "search_results": ["search_results", "search_result", "searchresults", "web_search", "web_search_results",
                       "search"],
}
# the development set's columns: the names the rules read
KNOWN = {
    "alerts": ["alert_id", "screened_at", "customer_name", "entity_type", "dob", "country", "city", "account_ref",
               "login_handle", "email", "hit_count", "hit_ids"],
    "hits": ["hit_id", "alert_id", "match_score", "entity_type", "name", "aliases", "gender", "dobs",
             "place_of_birth", "life_status", "deceased_date", "nationality", "citizenship", "countries", "addresses",
             "identifiers", "categories", "primary_category", "position_or_allegation", "details", "relationships",
             "profile_notes", "sources", "source_urls"],
    "customers": ["record_id", "entity_type", "full_name", "preferred_name", "login_name", "dob", "nationality",
                  "residence_country", "city", "address", "email", "phone", "occupation", "employer",
                  "registration_number", "onboarded_at", "status", "last_kyc_refresh"],
    "accounts": ["account_id", "customer_id", "login_handle", "phone", "opened_at", "status"],
    "onboarding": ["account_id", "customer_id", "kyc_applicant_id", "onboarded_at"],
    "kyc_checks": ["check_id", "applicant_id", "provider", "verified_name", "verified_dob", "document_type",
                   "document_number", "document_country", "result", "checked_at"],
    "notes": ["note_id", "requester_email", "created_at", "channel", "text"],
    "search_results": ["result_id", "query", "title", "snippet", "source"],
}
# fields without which a clearance could rest on half the picture
CRITICAL = {
    "alerts": ["alert_id"], "hits": ["hit_id", "name"], "customers": ["record_id", "full_name", "dob"],
    "accounts": ["account_id", "customer_id"], "onboarding": ["customer_id", "kyc_applicant_id"],
    "kyc_checks": ["check_id", "applicant_id", "verified_name", "verified_dob", "result"],
    "notes": ["note_id", "requester_email", "text"], "search_results": ["result_id", "query", "snippet"],
}
_S = lambda s: [x.strip() for x in s.split(",") if x.strip()]
SYNONYMS = {
    "alerts": {
        "alert_id": _S("id, alertid, alert_ref, alert_reference, alert_no, alert_number, screening_id, case_id"),
        "screened_at": _S("screening_date, screened_on, date_screened, screen_date, screened_date, alert_date, "
                          "screened, screening_timestamp, generated_at, raised_at"),
        "customer_name": _S("name, screened_name, full_name, customer, subject_name, client_name, party_name, "
                            "customer_full_name, input_name, screened_customer_name"),
        "entity_type": _S("type, customer_type, party_type, subject_type, customer_entity_type"),
        "dob": _S("date_of_birth, birth_date, birthdate, dob_screened, customer_dob, screened_dob, "
                  "date_of_incorporation, incorporation_date, customer_date_of_birth"),
        "country": _S("customer_country, screened_country, country_code, nationality, residence_country"),
        "city": _S("town, customer_city, screened_city"),
        "account_ref": _S("account, account_id, account_number, account_no, acct, acct_id, customer_account, "
                          "account_reference"),
        "login_handle": _S("login, username, user_name, handle, user, user_id, login_name, customer_login"),
        "email": _S("customer_email, email_address, e_mail, mail"),
        "hit_ids": _S("hits, hit_id_list, hit_list, matches, match_ids, hit_id, hitids"),
        "hit_count": _S("num_hits, n_hits, number_of_hits, hits_count"),
    },
    "hits": {
        "hit_id": _S("id, hitid, match_id, profile_id, hit_ref, hit_reference, entry_id, list_entry_id"),
        "alert_id": _S("alert, alert_ref, alertid, alert_reference, screening_id"),
        "match_score": _S("score, similarity, match_strength"),
        "entity_type": _S("type, profile_type, subject_type, record_type, hit_type, hit_entity_type, party_type"),
        "name": _S("full_name, hit_name, listed_name, primary_name, profile_name, matched_name, list_name, "
                   "watchlist_name, entity_name, hit_full_name"),
        "aliases": _S("alias, aka, akas, also_known_as, other_names, alternative_names, alternate_names, "
                      "hit_aliases, name_variants"),
        "dobs": _S("dob, dates_of_birth, date_of_birth, birth_dates, birth_date, birthdate, birthdates, "
                   "years_of_birth, year_of_birth, hit_dob, hit_dobs, hit_date_of_birth"),
        "place_of_birth": _S("birth_place, pob, birthplace, places_of_birth"),
        "life_status": _S("status, alive, living_status, vital_status"),
        "deceased_date": _S("date_of_death, death_date, dod, deceased_on, died_on"),
        "nationality": _S("nationalities"),
        "citizenship": _S("citizenships"),
        "countries": _S("country, country_list, country_codes, hit_countries, jurisdictions, locations, "
                        "linked_countries"),
        "addresses": _S("address, address_list, hit_addresses"),
        "categories": _S("category, list_type, lists, list_types, risk_categories"),
        "primary_category": _S("main_category"),
        "position_or_allegation": _S("position, allegation, role, title, positions, allegations"),
        "profile_notes": _S("notes, profile, biography, bio, description, remarks, narrative, profile_text, "
                            "further_information"),
        "sources": _S("source, source_names"),
        "source_urls": _S("urls, links, source_url"),
        "gender": _S("sex"),
    },
    "customers": {
        "record_id": _S("id, customer_id, customer_record_id, cust_id, record, customer_ref, client_id, party_id"),
        "entity_type": _S("type, customer_type, party_type"),
        "full_name": _S("name, customer_name, legal_name, registered_name, company_name"),
        "dob": _S("date_of_birth, birth_date, birthdate, date_of_incorporation, incorporation_date, "
                  "incorporated_on"),
        "residence_country": _S("country, country_of_residence, residence, residency, residential_country, "
                                "address_country"),
        "nationality": _S("citizenship, nationalities"),
        "email": _S("email_address, e_mail, mail"),
        "phone": _S("phone_number, mobile, telephone, tel, mobile_number, phone_no"),
        "city": _S("town"),
        "address": _S("street_address, address_line, residential_address"),
    },
    "accounts": {
        "account_id": _S("id, account, account_ref, account_number, account_no, acct_id"),
        "customer_id": _S("record_id, customer_record_id, cust_id, holder_id, customer, client_id, party_id"),
        "login_handle": _S("login, username, user_name, handle, user"),
        "phone": _S("phone_number, mobile"),
    },
    "onboarding": {
        "customer_id": _S("record_id, customer_record_id, cust_id, client_id"),
        "kyc_applicant_id": _S("applicant_id, kyc_id, applicant, kyc_applicant, idv_applicant_id"),
        "account_id": _S("account, account_ref, account_number"),
    },
    "kyc_checks": {
        "check_id": _S("id, kyc_check_id, kyc_id, verification_id, idv_id"),
        "applicant_id": _S("kyc_applicant_id, applicant"),
        "verified_name": _S("name, full_name, verified_full_name, document_name, name_on_document"),
        "verified_dob": _S("dob, date_of_birth, birth_date, verified_date_of_birth, document_dob"),
        "result": _S("status, outcome, check_result, decision, verification_result, check_status"),
        "document_country": _S("country, doc_country, issuing_country, document_issuing_country, id_country, "
                               "document_issuer_country"),
        "document_number": _S("doc_number, document_no, passport_number, id_number, doc_no"),
        "document_type": _S("doc_type, id_type"),
        "checked_at": _S("checked_on, date, check_date, created_at, verified_at, completed_at, timestamp"),
        "provider": _S("vendor, kyc_provider, source"),
    },
    "notes": {
        "note_id": _S("id, noteid, note_ref"),
        "requester_email": _S("email, customer_email, author_email, from_email, requester, email_address, "
                              "from, sender_email"),
        "text": _S("body, note, content, message, note_text, comment, details"),
        "created_at": _S("date, created_on, timestamp, created, filed_at"),
        "channel": _S("source, type, category, note_type, team"),
    },
    "search_results": {
        "result_id": _S("id, search_result_id, sr_id, resultid"),
        "query": _S("q, search_query, search_term, name, searched_name, term"),
        "snippet": _S("text, summary, description, body, content, excerpt"),
        "source": _S("url, link, domain, publisher"),
        "title": _S("heading, headline"),
    },
}
ALIASES = {t: {s: canon for canon, syns in m.items() for s in syns} for t, m in SYNONYMS.items()}
DATE_KEYS = {"dob", "dobs", "verified_dob", "screened_at", "deceased_date", "checked_at", "created_at",
             "onboarded_at", "opened_at", "last_kyc_refresh"}
LIST_KEYS = {"hit_ids"}
TYPE_MAP = {"individual": "Individual", "person": "Individual", "natural_person": "Individual", "i": "Individual",
            "p": "Individual", "human": "Individual", "people": "Individual", "private": "Individual",
            "company": "Company", "organisation": "Company", "organization": "Company", "entity": "Company",
            "business": "Company", "legal_person": "Company", "legal_entity": "Company", "corporate": "Company",
            "corporation": "Company", "c": "Company", "o": "Company", "firm": "Company"}
RESULT_MAP = {"approved": "approved", "approve": "approved", "pass": "approved", "passed": "approved",
              "verified": "approved", "success": "approved", "successful": "approved", "clear": "approved",
              "cleared": "approved", "ok": "approved", "accepted": "approved", "accept": "approved",
              "rejected": "rejected", "reject": "rejected", "fail": "rejected", "failed": "rejected",
              "declined": "rejected", "denied": "rejected", "refer": "rejected", "referred": "rejected",
              "expired": "expired", "pending": "pending", "in_progress": "pending", "review": "pending",
              "manual_review": "pending", "abandoned": "pending", "cancelled": "pending", "canceled": "pending"}
EPOCH = date(1899, 12, 30)


def snake(k):
    k = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(k).strip())
    k = re.sub(r"[^0-9a-zA-Z]+", "_", k).strip("_").lower()
    return re.sub(r"_+", "_", k)


def _date_value(v):
    """Numbers in date fields: a year, an Excel serial day, or a Unix timestamp."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 1800 <= v <= 2100:
            return str(int(v))
        if 1 <= v < 80000:
            return (EPOCH + timedelta(days=int(v))).isoformat()
        if v > 1e11:
            v = v / 1000
        if v > 1e8:
            return datetime.fromtimestamp(v, timezone.utc).date().isoformat()
        return None
    return v


def _scalar(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    return None if s in ("", "null", "None", "NULL", "nan", "NaN", "N/A", "n/a") else s


def _flatten(v):
    """Lists and dicts become the '; '-separated strings the rules read."""
    if isinstance(v, dict):
        tag = v.get("type") or v.get("tag") or v.get("kind")
        vals = [str(_flatten(x)) for k, x in v.items() if k not in ("type", "tag", "kind") and x not in (None, "")]
        return ", ".join(vals) + (f" [{tag}]" if tag else "")
    if isinstance(v, list):
        return "; ".join(str(_flatten(x)) for x in v if x not in (None, ""))
    return _scalar(v)


def _table_of(name):
    return name[:-6] if name.endswith(".jsonl") else name


def _map_key(table, k):
    """(canonical key, namespace) for a raw key; namespace 'alert' for alert fields carried on hit rows."""
    known = set(KNOWN.get(table, ()))
    al = ALIASES.get(table, {})
    if k in known:
        return k, None
    if k in al:
        return al[k], None
    if table == "hits":
        for pre in ("customer_", "alert_", "screened_", "screening_"):
            if k.startswith(pre):
                rest = k[len(pre):] if pre != "customer_" or k[len(pre):] != "name" else "customer_name"
                rest = "customer_name" if pre == "customer_" and rest in ("full_name",) else rest
                canon = rest if rest in KNOWN["alerts"] else ALIASES["alerts"].get(rest) or \
                    ALIASES["alerts"].get(k) or (k if k in KNOWN["alerts"] else None)
                if canon:
                    return canon, "alert"
        if k in ("screened_at", "account_ref", "login_handle", "customer_name", "hit_count"):
            return k, "alert"
    for pre in (table.rstrip("s") + "_", "hit_", "customer_", "kyc_", "alert_", "account_", "note_", "search_",
                "result_", "record_", "check_", "verified_"):
        if k.startswith(pre):
            rest = k[len(pre):]
            if rest in known:
                return rest, None
            if rest in al:
                return al[rest], None
    return k, None


def normalise_record(name, r, _prefix=""):
    """Map one raw record onto the canonical field names. Unknown fields are kept (and reported)."""
    table = _table_of(name)
    out, alert_part = {}, {}
    items = list(r.items())
    # flatten nested objects (but not address-like objects, which become one text)
    flat = []
    for k, v in items:
        sk = snake(k)
        if isinstance(v, dict) and not re.search(r"address|location|place", sk) and sk not in ("names",):
            for k2, v2 in v.items():
                flat.append((f"{sk}_{snake(k2)}", v2, snake(k2)))
        else:
            flat.append((sk, v, None))
    for sk, v, inner in flat:
        canon, ns = _map_key(table, sk)
        if canon not in KNOWN.get(table, ()) and ns is None and inner:
            c2, ns2 = _map_key(table, inner)          # {"customer": {"dob": ..}} -> dob
            if c2 in KNOWN.get(table, ()) or ns2:
                canon, ns = c2, ns2
        if table == "alerts" and canon == "hit_ids" and isinstance(v, list) and v and isinstance(v[0], dict):
            NESTED_HITS.extend(v)                     # hits nested inside the alert
            out.setdefault("_nested_hits", []).extend(v)
            continue
        target = alert_part if ns == "alert" else out
        if canon in target and target[canon] not in (None, "", []):
            continue                                  # an alias never overwrites a real field
        if canon in LIST_KEYS:
            if isinstance(v, list):
                val = [str(_scalar(x)) for x in v if _scalar(x)]
            elif isinstance(v, str) and v.strip().startswith("["):
                try:
                    val = [str(x).strip() for x in json.loads(v) if str(x).strip()]
                except ValueError:
                    val = [x for x in re.split(r"[;,|\s\[\]\"']+", v) if x]
            else:
                s = _scalar(v)
                val = [x for x in re.split(r"[;,|\s]+", s) if x] if s else []
            target[canon] = val
            continue
        if canon in DATE_KEYS:
            v = [_date_value(x) for x in v] if isinstance(v, list) else _date_value(v)
        val = _flatten(v)
        if isinstance(val, str) and canon not in DATE_KEYS and val.startswith("[") and val.endswith("]"):
            try:
                val = _flatten(json.loads(val))
            except ValueError:
                pass
        if canon == "entity_type" and val is not None:
            val = TYPE_MAP.get(snake(val), None)      # unknown type -> unknown (never a type mismatch)
        if table == "kyc_checks" and canon == "result" and val is not None:
            val = RESULT_MAP.get(snake(val), "unrecognised:" + str(val))
        if table == "hits" and canon == "life_status" and isinstance(v, bool):
            val = "Deceased" if (sk in ("deceased", "is_deceased", "dead")) == v else "Alive"
        target[canon] = val
    for k in out:
        if not k.startswith("_") and k not in KNOWN.get(table, ()):
            UNMAPPED.setdefault(table, {}).setdefault(k, 0)
            UNMAPPED[table][k] += 1
    if alert_part:
        out["_alert"] = alert_part
    for idk in ("alert_id", "hit_id", "record_id", "account_id", "customer_id", "kyc_applicant_id", "applicant_id",
                "check_id", "note_id", "result_id"):
        if isinstance(out.get(idk), str):
            out[idk] = out[idk].strip()
    return out


# ---------------------------------------------------------------- readers
def _read_text(p):
    with open(p, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        LOAD_WARNINGS.append(f"{os.path.basename(p)}: not UTF-8, read as cp1252")
        return raw.decode("cp1252", errors="replace")


def _rows_from_json_text(text, label):
    t = text.strip()
    if not t:
        return []
    if t[0] in "[{":
        try:
            obj = json.loads(t)
            if isinstance(obj, list):
                return [r for r in obj if isinstance(r, dict)]
            if isinstance(obj, dict):
                if len(obj) <= 3:                       # a wrapper such as {"data": [...], "count": n}
                    for k in ("data", "records", "rows", "items", "results"):
                        if isinstance(obj.get(k), list):
                            return [r for r in obj[k] if isinstance(r, dict)]
                return [obj]
        except ValueError:
            pass
    rows = []
    for i, line in enumerate(t.splitlines(), 1):
        line = line.strip().rstrip(",")
        if not line or line in "[]":
            continue
        try:
            r = json.loads(line)
        except ValueError:
            LOAD_WARNINGS.append(f"{label}:{i}: bad JSON line skipped")
            continue
        if isinstance(r, dict):
            rows.append(r)
        else:
            LOAD_WARNINGS.append(f"{label}:{i}: not an object, skipped")
    return rows


def _pick_header(grid, table):
    known = set(KNOWN.get(table, ())) | set(ALIASES.get(table, {}))
    best, best_i = -1, 0
    for i, row in enumerate(grid[:15]):
        score = sum(1 for c in row if c not in (None, "") and snake(c) in known)
        if score > best:
            best, best_i = score, i
    return best_i


def _grid_to_rows(grid, table):
    grid = [r for r in grid if any(c not in (None, "") for c in r)]
    if not grid:
        return []
    h = _pick_header(grid, table)
    header = [str(c).strip() if c not in (None, "") else f"col{j}" for j, c in enumerate(grid[h])]
    rows = []
    for r in grid[h + 1:]:
        d = {header[j]: r[j] for j in range(min(len(header), len(r))) if r[j] not in (None, "")}
        if d:
            rows.append(d)
    return rows


def _read_csv(p, table):
    text = _read_text(p)
    first = text.split("\n", 1)[0]
    counts = {d: len(re.findall(re.escape(d) + r'(?=(?:[^"]*"[^"]*")*[^"]*$)', first)) for d in (",", ";", "\t", "|")}
    delim = max(sorted(counts), key=lambda d: counts[d]) if any(counts.values()) else ","
    grid = list(csv.reader(io.StringIO(text), delimiter=delim, quotechar='"'))
    return _grid_to_rows(grid, table)


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def xlsx_sheets(p):
    """{sheet name: grid} from an .xlsx file using only zipfile + XML."""
    out = {}
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[r.get("Id")] = r.get("Target")
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        for s in wb.find("m:sheets", _NS):
            target = rels.get(s.get(_RNS), "")
            target = target.lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            if path not in names:
                continue
            grid = []
            for row in ET.fromstring(z.read(path)).iter(f"{{{_NS['m']}}}row"):
                cells = {}
                for c in row.findall("m:c", _NS):
                    ref, t = c.get("r", ""), c.get("t")
                    col = 0
                    for ch in re.match(r"[A-Z]*", ref).group(0):
                        col = col * 26 + ord(ch) - 64
                    col = col - 1 if col else len(cells)
                    v = c.find("m:v", _NS)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
                    elif v is not None and v.text is not None:
                        val = v.text
                        if t not in ("str", "b", "e"):
                            try:
                                f = float(val)
                                val = int(f) if f.is_integer() else f
                            except ValueError:
                                pass
                    else:
                        val = None
                    cells[col] = val
                if cells:
                    grid.append([cells.get(j) for j in range(max(cells) + 1)])
            out[s.get("name")] = grid
    return out


# ---------------------------------------------------------------- discovery
_INDEX = {}


def _stem_table(stem):
    s = snake(stem)
    for t, stems in TABLE_STEMS.items():
        if s in stems:
            return t
    best = None                                    # dev_alerts, screening_hits, unseen_kyc_checks ...
    for t, stems in TABLE_STEMS.items():
        for st in stems:
            m = re.search(rf"(^|_){re.escape(st)}($|_)", s)
            if len(st) >= 4 and m and "label" not in s:
                cand = (m.end() == len(s), len(st), t)       # a match at the end of the name wins, then the longest
                if best is None or cand > best:
                    best = cand
    return best[2] if best else None


def _index(folder):
    key = os.path.abspath(folder)
    if key in _INDEX:
        return _INDEX[key]
    files = []
    base_depth = key.rstrip(os.sep).count(os.sep)
    for root, dirs, fs in os.walk(key):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("output", "__pycache__"))
        if root.count(os.sep) - base_depth >= 2:
            dirs[:] = []
        for f in sorted(fs):
            files.append(os.path.join(root, f))
    idx = {"single": {}, "multi": []}
    rank = {".jsonl": 0, ".ndjson": 0, ".json": 1, ".csv": 2, ".tsv": 2, ".txt": 3, ".xlsx": 4}
    for p in files:
        stem, ext = os.path.splitext(os.path.basename(p))
        ext = ext.lower()
        if ext not in rank or "label" in stem.lower() or stem.lower() in ("name_variants", "policy"):
            continue
        t = _stem_table(stem)
        depth = p[len(key):].count(os.sep)
        if t:
            cand = (depth, rank[ext], p)
            if t not in idx["single"] or cand < idx["single"][t]:
                idx["single"][t] = cand
        elif ext in (".xlsx", ".json"):
            idx["multi"].append(p)
    _INDEX[key] = idx
    return idx


def _raw_rows(folder, table):
    idx = _index(folder)
    if table in idx["single"]:
        p = idx["single"][table][2]
        ext = os.path.splitext(p)[1].lower()
        label = os.path.basename(p)
        try:
            if ext in (".csv", ".tsv"):
                return _read_csv(p, table), label
            if ext == ".xlsx":
                sheets = xlsx_sheets(p)
                named = [g for n, g in sheets.items() if _stem_table(n) == table]
                grid = named[0] if named else (max(sheets.values(), key=len) if sheets else [])
                return _grid_to_rows(grid, table), label
            text = _read_text(p)
            if ext == ".txt" and text.lstrip()[:1] not in "[{":
                return _read_csv(p, table), label
            return _rows_from_json_text(text, label), label
        except Exception as e:                                  # unreadable file: reported, fail-safe
            LOAD_WARNINGS.append(f"{label}: could not be read ({type(e).__name__})")
            DEGRADED.append(f"{label} could not be read")
            return [], label
    for p in idx["multi"]:                                       # workbook with one sheet per table, or JSON dict
        try:
            if p.lower().endswith(".xlsx"):
                for sheet, grid in xlsx_sheets(p).items():
                    if _stem_table(sheet) == table:
                        return _grid_to_rows(grid, table), f"{os.path.basename(p)}[{sheet}]"
            else:
                obj = json.loads(_read_text(p))
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if _stem_table(k) == table and isinstance(v, list):
                            return [r for r in v if isinstance(r, dict)], f"{os.path.basename(p)}[{k}]"
        except Exception as e:
            LOAD_WARNINGS.append(f"{os.path.basename(p)}: could not be read ({type(e).__name__})")
    return None, None


def load_table(folder, table):
    rows, label = _raw_rows(folder, table)
    if rows is None:
        if table in ("alerts", "hits"):
            LOAD_WARNINGS.append(f"{table}: no file found")
        else:
            LOAD_WARNINGS.append(f"{table}: no file found (no clearances will be made)")
            DEGRADED.append(f"no {table.replace('_', ' ')} file was found")
        return []
    out = []
    for r in rows:
        try:
            out.append(normalise_record(table + ".jsonl", r))
        except Exception as e:
            LOAD_WARNINGS.append(f"{label}: a row could not be mapped ({type(e).__name__}), skipped")
    check_critical(table, out, label)
    return out


def check_critical(table, rows, label):
    if not rows:
        return
    for f in CRITICAL.get(table, ()):
        if table == "alerts" and f == "alert_id":
            continue
        if not any(r.get(f) not in (None, "", []) for r in rows):
            LOAD_WARNINGS.append(f"{label}: no field could be mapped to '{f}' (no clearances will be made)")
            DEGRADED.append(f"the {table.replace('_', ' ')} field '{f}' could not be found")
    if table == "kyc_checks":
        odd = sorted({r["result"] for r in rows if str(r.get("result") or "").startswith("unrecognised:")})
        if odd:
            LOAD_WARNINGS.append(f"{label}: unrecognised check results {odd[:5]} (no clearances will be made)")
            DEGRADED.append("some identity-check results could not be interpreted")


def load_jsonl(folder, name):
    """Back-compatible entry point: load one table by its brief file name (e.g. 'hits.jsonl')."""
    return load_table(folder, _table_of(name))


def reset():
    LOAD_WARNINGS.clear(); DEGRADED.clear(); UNMAPPED.clear(); NESTED_HITS.clear(); _INDEX.clear()


def summary_lines():
    out = []
    for t in TABLES:
        if UNMAPPED.get(t):
            fs = ", ".join(f"{k} ({n})" for k, n in sorted(UNMAPPED[t].items())[:15])
            out.append(f"unmapped fields in {t} (kept, not used): {fs}")
    return out

FIELD_ALIASES = {t + ".jsonl": m for t, m in ALIASES.items()}   # kept for older callers


def load_alerts_and_hits(folder):
    """Alerts and hits, whichever way they arrive: separate files, hits nested in alerts, or hit rows carrying
    the alert's fields (alert rows are then rebuilt). Ids are kept exactly as given."""
    alerts = load_table(folder, "alerts")
    hits_rows = load_table(folder, "hits")
    hits, order = {}, []
    no_id = 0
    for h in hits_rows:
        if not h.get("hit_id"):
            no_id += 1
            continue
        if h["hit_id"] not in hits:
            hits[h["hit_id"]] = h
            order.append(h["hit_id"])
    for a in alerts:
        for raw in a.pop("_nested_hits", []):
            h = normalise_record("hits.jsonl", raw)
            if not h.get("hit_id"):
                no_id += 1
                continue
            if a.get("alert_id"):
                h.setdefault("alert_id", a["alert_id"])
                h["alert_id"] = h.get("alert_id") or a["alert_id"]
            ids = a.setdefault("hit_ids", [])
            if isinstance(ids, list) and h["hit_id"] not in ids:
                ids.append(h["hit_id"])
            if h["hit_id"] not in hits:
                hits[h["hit_id"]] = h
    if no_id:
        LOAD_WARNINGS.append(f"{no_id} hit row(s) have no recognisable hit id and cannot be answered")
    by_id = {}
    for a in alerts:
        if a.get("alert_id"):
            by_id.setdefault(a["alert_id"], a)
    # hits without an alert id: take it from the alert that lists them
    for a in by_id.values():
        for hid in a.get("hit_ids") or []:
            if hid in hits and not hits[hid].get("alert_id"):
                hits[hid]["alert_id"] = a["alert_id"]
    # alert rows rebuilt (or completed) from alert fields carried on hit rows
    for hid in sorted(hits):
        h = hits[hid]
        part = h.pop("_alert", None) or {}
        aid = h.get("alert_id")
        if not aid:
            continue
        a = by_id.get(aid)
        if a is None:
            a = {"alert_id": aid}
            by_id[aid] = a
            alerts.append(a)
        for k, v in part.items():
            if a.get(k) in (None, "", []):
                a[k] = v
    if alerts and hits and not any(a.get("customer_name") for a in alerts):
        LOAD_WARNINGS.append("alerts: no screened customer name could be found")
    check_critical("hits", list(hits.values()), "hits")
    return alerts, hits
