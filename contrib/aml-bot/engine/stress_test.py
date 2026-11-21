#!/usr/bin/env python3
"""Perturbation stress test (standard library only).

Rewrites the data folder in ways that should NOT make anyone less suspicious (date formats, name order,
diacritics, country codes, renamed fields, lists vs strings, junk lines, ...), re-runs the screener and counts
decision flips against the unperturbed run.  An UNSAFE flip is any hit that was SAME_ENTITY or REVIEW before and
is DIFFERENT_ENTITY after: the perturbation manufactured a clearance.  Target: 0 unsafe flips.

    python3 stress_test.py <data folder> [perturbation ...]
"""
import json, os, random, shutil, sys, tempfile, unicodedata, collections, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import screen  # noqa: E402

MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December"]
DATE_FIELDS = {"alerts.jsonl": ["dob", "screened_at"], "customers.jsonl": ["dob"], "kyc_checks.jsonl": ["verified_dob"],
               "hits.jsonl": ["dobs", "deceased_date"]}
ACCENT = {"a": "á", "e": "é", "i": "í", "o": "ö", "u": "ü", "c": "ç", "n": "ñ"}


def read(folder, name):
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def write(folder, name, rows, junk=False):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        if junk:
            fh.write("\ufeff")
        for i, r in enumerate(rows):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if junk and i % 97 == 0:
                fh.write("{this is not json\n[1,2,3]\n\n")


def iso_parts(v):
    import re
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(v).strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def refmt(v, style):
    p = iso_parts(v)
    if not p:
        return v
    y, m, d = p
    return {"long": f"{d} {MONTH[m-1]} {y}", "us": f"{MONTH[m-1]} {d}, {y}", "slash_unamb": f"{y}/{m:02d}/{d:02d}",
            "dotted": f"{d:02d}.{m:02d}.{y}" if d > 12 else f"{y}.{m:02d}.{d:02d}",
            "two_digit": f"{d:02d}/{m:02d}/{y % 100:02d}", "us_slash": f"{m:02d}/{d:02d}/{y}",
            "year_only": f"{y}"}[style]


def map_dates(tables, style):
    for name, fields in DATE_FIELDS.items():
        for r in tables.get(name, []):
            for f in fields:
                if isinstance(r.get(f), str):
                    r[f] = "; ".join(refmt(x.strip(), style) for x in r[f].split(";"))


def swap_order(n):
    if not isinstance(n, str) or not n.strip():
        return n
    if "," in n:
        a, b = n.split(",", 1)
        return f"{b.strip()} {a.strip()}"
    parts = n.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else n


def accent(n):
    return "".join(ACCENT.get(ch, ch) if i % 3 == 0 else ch for i, ch in enumerate(n)) if isinstance(n, str) else n


def strip_accents(n):
    return "".join(c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c)) if isinstance(n, str) else n


def to_code(v, kind):
    cc = screen.country_code(v) if isinstance(v, str) else None
    if not cc or cc == screen.UNKNOWN_COUNTRY:
        return v
    aliases = COUNTRIES.get(cc, [])
    pick = {"alpha2": cc, "alpha3": next((a for a in aliases if len(a) == 3 and a.isupper()), cc),
            "upper": (screen.country_name(cc) or v).upper()}[kind]
    return pick


COUNTRIES = json.load(open(os.path.join(HERE, "countries.json"), encoding="utf-8"))


def perturb(tables, kind, rng, variants):
    junk = False
    if kind.startswith("dates_"):
        map_dates(tables, kind[6:])
    elif kind == "name_order":
        for h in tables["hits.jsonl"]:
            h["name"] = swap_order(h.get("name"))
        for a in tables["alerts.jsonl"]:
            a["customer_name"] = swap_order(a.get("customer_name"))
    elif kind == "diacritics_add":
        for h in tables["hits.jsonl"]:
            h["name"] = accent(h.get("name"))
    elif kind == "diacritics_strip":
        for t in ("hits.jsonl", "customers.jsonl", "kyc_checks.jsonl", "alerts.jsonl"):
            for r in tables[t]:
                for f in ("name", "aliases", "full_name", "verified_name", "customer_name"):
                    r[f] = strip_accents(r.get(f)) if f in r else r.get(f, None)
                    if r[f] is None and f not in r:
                        del r[f]
    elif kind == "transliterate":
        for h in tables["hits.jsonl"]:
            parts = (h.get("name") or "").split()
            new = []
            for p in parts:
                g = variants.get(p.lower().strip(","))
                new.append(rng.choice(sorted(g)).capitalize() + ("," if p.endswith(",") else "") if g else p)
            h["name"] = " ".join(new)
    elif kind.startswith("country_"):
        k = kind[8:]
        for h in tables["hits.jsonl"]:
            for f in ("nationality", "citizenship", "countries"):
                if isinstance(h.get(f), str):
                    h[f] = "; ".join(to_code(x.strip(), k) for x in h[f].split(";"))
        for c in tables["customers.jsonl"]:
            for f in ("nationality", "residence_country"):
                c[f] = to_code(c.get(f), k)
        for kc in tables["kyc_checks.jsonl"]:
            kc["document_country"] = to_code(kc.get("document_country"), k)
    elif kind == "null_non_identity":
        for h in tables["hits.jsonl"]:
            for f in ("match_score", "gender", "identifiers", "relationships", "sources", "source_urls",
                      "position_or_allegation", "primary_category"):
                h[f] = None
        for c in tables["customers.jsonl"]:
            for f in ("occupation", "employer", "status", "last_kyc_refresh", "preferred_name", "login_name"):
                c[f] = None
    elif kind == "case_whitespace":
        for t, fs in (("hits.jsonl", ("name", "aliases", "nationality", "countries")),
                      ("customers.jsonl", ("full_name", "nationality", "residence_country")),
                      ("alerts.jsonl", ("customer_name", "country")), ("kyc_checks.jsonl", ("verified_name",))):
            for r in tables[t]:
                for f in fs:
                    if isinstance(r.get(f), str):
                        r[f] = "  " + r[f].upper().replace(" ", "   ") + " "
    elif kind == "extra_fields":
        for t in tables.values():
            for r in t:
                r["x_extra"] = rng.choice([None, "", 0, [], {"a": 1}, "noise"])
                r["Comment"] = "added by stress test"
    elif kind == "renamed_fields":
        ren = {"alerts.jsonl": {"dob": "date_of_birth", "hit_ids": "hits", "account_ref": "account_id",
                                "customer_name": "name"},
               "hits.jsonl": {"dobs": "dates_of_birth", "aliases": "aka", "profile_notes": "notes",
                              "categories": "category"},
               "customers.jsonl": {"dob": "Date of Birth", "residence_country": "country_of_residence"},
               "kyc_checks.jsonl": {"result": "Outcome"}, "notes.jsonl": {"text": "body"}}
        for t, m in ren.items():
            for r in tables[t]:
                for a, b in m.items():
                    if a in r:
                        r[b] = r.pop(a)
    elif kind == "list_vs_string":
        for h in tables["hits.jsonl"]:
            for f in ("aliases", "dobs", "nationality", "citizenship", "countries", "addresses", "categories"):
                if isinstance(h.get(f), str):
                    h[f] = [x.strip() for x in h[f].split(";") if x.strip()]
    elif kind == "junk_lines":
        junk = True
    elif kind == "combined":
        for k in ("dates_long", "name_order", "diacritics_add", "country_alpha3", "extra_fields", "list_vs_string"):
            perturb(tables, k, rng, variants)
        junk = True
    return junk


KINDS = ["dates_long", "dates_us", "dates_slash_unamb", "dates_dotted", "dates_two_digit", "dates_us_slash",
         "dates_year_only", "name_order", "diacritics_add", "diacritics_strip", "transliterate", "country_alpha2",
         "country_alpha3", "country_upper", "null_non_identity", "case_whitespace", "extra_fields",
         "renamed_fields", "list_vs_string", "junk_lines", "combined"]
TABLES = ["alerts.jsonl", "hits.jsonl", "customers.jsonl", "accounts.jsonl", "onboarding.jsonl", "kyc_checks.jsonl",
          "notes.jsonl", "search_results.jsonl"]


def run(folder):
    out = os.path.join(tempfile.mkdtemp(), "out.jsonl")
    screen.LOAD_WARNINGS.clear()
    with contextlib.redirect_stderr(io.StringIO()):
        screen.main(folder, out)
    with open(out, encoding="utf-8") as fh:
        return {r["hit_id"]: r for r in map(json.loads, fh)}


def main(src, kinds):
    base_tables = {t: read(src, t) for t in TABLES}
    nv = json.load(open(os.path.join(src, "name_variants.json"), encoding="utf-8"))
    variants = {}
    for g in nv.get("given_name_groups", []):
        for n in g:
            variants[n.lower()] = set(g)
    base = run(src)
    print(f"baseline: {len(base)} hits, {sum(r['decision'] == 'DIFFERENT_ENTITY' for r in base.values())} cleared")
    total_unsafe, report = 0, {}
    for kind in kinds:
        rng = random.Random(7)
        tmp = tempfile.mkdtemp(prefix=f"stress_{kind}_")
        tables = json.loads(json.dumps(base_tables))
        junk = perturb(tables, kind, rng, variants)
        for t, rows in tables.items():
            write(tmp, t, rows, junk=junk)
        for extra in os.listdir(src):
            if extra not in TABLES and os.path.isfile(os.path.join(src, extra)):
                shutil.copy(os.path.join(src, extra), tmp)
        new = run(tmp)
        flips = collections.Counter()
        unsafe = []
        for h, r in base.items():
            n = new.get(h)
            nd = n["decision"] if n else "MISSING"
            if nd != r["decision"]:
                flips[f"{r['decision']}->{nd}"] += 1
                if nd == "DIFFERENT_ENTITY":
                    unsafe.append(h)
        total_unsafe += len(unsafe)
        report[kind] = {"flips": dict(flips), "unsafe": unsafe}
        print(f"{kind:20s} unsafe={len(unsafe):3d}  flips={dict(flips)}" + (f"  e.g. {unsafe[:5]}" if unsafe else ""))
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"TOTAL UNSAFE FLIPS: {total_unsafe}")
    return report, total_unsafe


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rep, unsafe = main(sys.argv[1], sys.argv[2:] or KINDS)
    with open(os.path.join(tempfile.gettempdir(), "stress_report.json"), "w") as fh:
        json.dump(rep, fh, indent=1)
    sys.exit(1 if unsafe else 0)
