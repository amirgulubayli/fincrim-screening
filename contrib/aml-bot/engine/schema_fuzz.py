#!/usr/bin/env python3
"""Schema-fuzz test (standard library only).

The official input format is not published, so this rewrites a data folder in several plausible shapes and
checks that the screener (a) never crashes, (b) makes 0 unsafe clearances against the practice labels and never
clears a hit the clean run did not clear, and (c) gives identical decisions wherever the data is equivalent.

    python3 schema_fuzz.py <data folder> [variant ...]
"""
import contextlib, csv, io, json, os, random, shutil, subprocess, sys, tempfile, collections

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = ["alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def read(folder, t):
    with open(os.path.join(folder, t + ".jsonl"), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def camel(k):
    p = k.split("_")
    return p[0] + "".join(x.title() for x in p[1:])


RENAME = {  # synonyms a different exporter might use
    "alerts": {"alert_id": "AlertID", "customer_name": "screenedName", "dob": "dateOfBirth", "hit_ids": "hits",
               "account_ref": "accountNumber", "screened_at": "screeningDate", "entity_type": "customerType"},
    "hits": {"hit_id": "id", "name": "fullName", "dobs": "datesOfBirth", "aliases": "aka", "countries": "country",
             "profile_notes": "biography", "deceased_date": "dateOfDeath", "entity_type": "type"},
    "customers": {"record_id": "customerId", "full_name": "legalName", "dob": "birthDate",
                  "residence_country": "countryOfResidence", "phone": "mobileNumber"},
    "accounts": {"account_id": "Account Number", "customer_id": "Customer Record ID"},
    "onboarding": {"kyc_applicant_id": "applicantId", "customer_id": "Customer ID"},
    "kyc_checks": {"check_id": "verificationId", "verified_dob": "documentDob", "result": "Status",
                   "document_country": "issuingCountry", "applicant_id": "Applicant"},
    "notes": {"note_id": "Note ID", "requester_email": "customerEmail", "text": "body"},
    "search_results": {"result_id": "id", "query": "searchTerm", "snippet": "summary"},
}


def odd_date(v, i):
    import re
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if not m:
        return v
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    return [f"{d} {MONTHS[mo-1]} {y}", f"{MONTHS[mo-1][:3]} {d}, {y}", f"{y}/{mo:02d}/{d:02d}",
            f"{y}-{mo:02d}-{d:02d}T00:00:00Z", f"{d}{'th' if d not in (1, 2, 3, 21, 22, 23, 31) else ''} {MONTHS[mo-1]} {y}"][i % 5]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_csv(path, rows, delim=","):
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delim)
        w.writerow(keys)
        for r in rows:
            w.writerow(["; ".join(map(str, r[k])) if isinstance(r.get(k), list) else ("" if r.get(k) is None else r[k])
                        for k in keys])


def build(src, dst, variant):
    T = {t: read(src, t) for t in TABLES}
    rng = random.Random(11)
    expect_equal = True
    if variant == "renamed_camel":
        for t, rows in T.items():
            m = RENAME.get(t, {})
            T[t] = [{m.get(k, camel(k)): v for k, v in r.items()} for r in rows]
        for t, rows in T.items():
            write_jsonl(os.path.join(dst, t + ".jsonl"), rows)
    elif variant == "csv_files":
        for i, (t, rows) in enumerate(T.items()):
            write_csv(os.path.join(dst, t.replace("_", " ").title() + ".csv"), rows, delim=";" if i % 2 else ",")
    elif variant == "json_arrays_shuffled_extra":
        for t, rows in T.items():
            rows = [dict(sorted(r.items(), key=lambda kv: rng.random()), source_system="CORE", x_row=i)
                    for i, r in enumerate(rows)]
            rng.shuffle(rows)
            os.makedirs(os.path.join(dst, "export"), exist_ok=True)
            with open(os.path.join(dst, "export", f"dev_{t}.json"), "w", encoding="utf-8") as fh:
                json.dump({"data": rows}, fh, ensure_ascii=False)
    elif variant == "nested_hits_in_alerts":
        hits = {h["hit_id"]: h for h in T["hits"]}
        alerts = []
        for a in T["alerts"]:
            a = dict(a)
            ids = a.pop("hit_ids")
            a["customer"] = {"name": a.pop("customer_name"), "dob": a.pop("dob"), "country": a.pop("country"),
                             "city": a.pop("city")}
            a["matches"] = [{k: v for k, v in hits[h].items() if k != "alert_id"} for h in ids]
            alerts.append(a)
        with open(os.path.join(dst, "alerts.json"), "w", encoding="utf-8") as fh:
            json.dump(alerts, fh, ensure_ascii=False)
        T["customers"] = [dict({k: v for k, v in c.items() if k not in ("email", "phone")},
                               contact={"email": c.get("email"), "phone": c.get("phone")}) for c in T["customers"]]
        for t in TABLES[2:]:
            write_jsonl(os.path.join(dst, t + ".jsonl"), T[t])
    elif variant == "flat_hit_rows_joined":
        alerts = {a["alert_id"]: a for a in T["alerts"]}
        rows = []
        for h in T["hits"]:
            a = alerts[h["alert_id"]]
            r = {f"hit_{k}" if k not in ("hit_id", "alert_id") else k: v for k, v in h.items()}
            r.update({"customer_name": a["customer_name"], "customer_dob": a["dob"], "customer_country": a["country"],
                      "customer_city": a["city"], "screened_at": a["screened_at"], "account_ref": a["account_ref"],
                      "login_handle": a["login_handle"], "customer_email": a["email"],
                      "customer_entity_type": a["entity_type"]})
            rows.append(r)
        write_jsonl(os.path.join(dst, "screening_hits.jsonl"), rows)
        for t in TABLES[2:]:
            write_jsonl(os.path.join(dst, t + ".jsonl"), T[t])
    elif variant == "odd_dates_missing_optional":
        drop = {"hits": ("gender", "identifiers", "sources", "source_urls", "match_score", "relationships"),
                "customers": ("occupation", "employer", "status", "last_kyc_refresh"), "alerts": ("hit_count",)}
        fields = {"alerts": ("dob", "screened_at"), "customers": ("dob",), "kyc_checks": ("verified_dob",),
                  "hits": ("deceased_date",)}
        for t, rows in T.items():
            for i, r in enumerate(rows):
                for f in drop.get(t, ()):
                    r.pop(f, None)
                for f in fields.get(t, ()):
                    if f in r:
                        r[f] = odd_date(r[f], i)
            write_jsonl(os.path.join(dst, t + ".jsonl"), rows)
    elif variant == "critical_field_missing":
        expect_equal = False                   # verified DOBs unreadable: nothing may be newly cleared
        for r in T["kyc_checks"]:
            r["dob_on_doc"] = r.pop("verified_dob")
            r["dob_on_doc"] = "redacted"
        for t, rows in T.items():
            write_jsonl(os.path.join(dst, t + ".jsonl"), rows)
    elif variant == "unknown_result_values":
        expect_equal = False
        for r in T["kyc_checks"]:
            r["result"] = {"approved": "GREEN", "rejected": "RED", "expired": "AMBER"}.get(r["result"], r["result"])
        for t, rows in T.items():
            write_jsonl(os.path.join(dst, t + ".jsonl"), rows)
    else:
        raise SystemExit(f"unknown variant {variant}")
    for extra in ("name_variants.json", "POLICY.md"):
        if os.path.exists(os.path.join(src, extra)):
            shutil.copy(os.path.join(src, extra), dst)
    return expect_equal


VARIANTS = ["renamed_camel", "csv_files", "json_arrays_shuffled_extra", "nested_hits_in_alerts",
            "flat_hit_rows_joined", "odd_dates_missing_optional", "critical_field_missing", "unknown_result_values"]


def run(folder):
    out = os.path.join(tempfile.mkdtemp(), "out.jsonl")
    p = subprocess.run([sys.executable, os.path.join(HERE, "screen.py"), folder, out], capture_output=True, text=True)
    rows = {}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            rows = {r["hit_id"]: r for r in map(json.loads, fh)}
    return p.returncode, rows, p.stderr


def main(src, variants):
    labels = {}
    lp = os.path.join(src, "practice_labels.jsonl")
    if os.path.exists(lp):
        with open(lp, encoding="utf-8") as fh:
            labels = {r["hit_id"]: r["decision"] for r in map(json.loads, fh)}
    code, base, _ = run(src)
    key = lambda r: (r["decision"], r["reason_code"], r["customer_record_id"])
    total_bad = 0
    print(f"baseline: {len(base)} hits, {sum(r['decision'] == 'DIFFERENT_ENTITY' for r in base.values())} cleared")
    for v in variants:
        dst = tempfile.mkdtemp(prefix=f"fuzz_{v}_")
        equal = build(src, dst, v)
        code, new, err = run(dst)
        crashed = code != 0
        missing = len(set(base) - set(new))
        unsafe_truth = [h for h, d in labels.items() if d != "DIFFERENT_ENTITY" and
                        new.get(h, {}).get("decision") == "DIFFERENT_ENTITY"]
        new_clear = [h for h in base if base[h]["decision"] != "DIFFERENT_ENTITY" and
                     new.get(h, {}).get("decision") == "DIFFERENT_ENTITY"]
        same = sum(1 for h in base if h in new and key(base[h]) == key(new[h]))
        flips = collections.Counter(f"{base[h]['decision']}->{new[h]['decision']}" for h in base
                                    if h in new and base[h]["decision"] != new[h]["decision"])
        bad = crashed or missing or unsafe_truth or new_clear or (equal and same != len(base))
        total_bad += bool(bad)
        print(f"{v:28s} {'FAIL' if bad else 'ok  '} crash={crashed} missing={missing} "
              f"unsafe_vs_labels={len(unsafe_truth)} new_clearances={len(new_clear)} "
              f"identical={same}/{len(base)}{'' if equal else ' (not equivalent data)'} flips={dict(flips)}")
        warn = [l for l in err.splitlines() if l.startswith("warning:")]
        if warn:
            print("    " + warn[-1][:200])
        shutil.rmtree(dst, ignore_errors=True)
    print("ALL VARIANTS PASS" if not total_bad else f"{total_bad} VARIANT(S) FAILED")
    return total_bad


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(1 if main(sys.argv[1], sys.argv[2:] or VARIANTS) else 0)
