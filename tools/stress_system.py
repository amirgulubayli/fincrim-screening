#!/usr/bin/env python3
"""Perturbation stress test for OUR scored system (standard library only).

Reuses the 21 perturbations of contrib/aml-bot/engine/stress_test.py (copied below so this
tool does not depend on the contrib engine), but runs `system/screen.py run` - the combined
system (decision core + independent modular check) - in fresh subprocesses on each perturbed
copy. Perturbed copies are written to a temp dir OUTSIDE the repo and deleted afterwards.

An UNSAFE flip is a hit that was SAME_ENTITY or REVIEW on the clean data and is
DIFFERENT_ENTITY on the perturbed copy: the perturbation manufactured a clearance.
A CRASH (non-zero exit / missing output) is reported separately. Target: 0 unsafe, 0 crashes.

    uv run python tools/stress_system.py <data folder> [--kinds k1 k2 ...]
        [--modes combined check] [--workers 4] [--report out.json] [--markdown]

Exit code 1 if any unsafe flip or crash in the `combined` mode (the scored one).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREEN_PY = ROOT / "system" / "screen.py"
CORE_DIR = ROOT / "system" / "screening" / "core"
sys.path.insert(0, str(CORE_DIR))
import aml_engine as E  # noqa: E402  - only for country_code/country_name (perturbation helper)

MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
         "October", "November", "December"]
DATE_FIELDS = {"alerts.jsonl": ["dob", "screened_at"], "customers.jsonl": ["dob"],
               "kyc_checks.jsonl": ["verified_dob"], "hits.jsonl": ["dobs", "deceased_date"]}
ACCENT = {"a": "á", "e": "é", "i": "í", "o": "ö", "u": "ü", "c": "ç", "n": "ñ"}
TABLES = ["alerts.jsonl", "hits.jsonl", "customers.jsonl", "accounts.jsonl", "onboarding.jsonl",
          "kyc_checks.jsonl", "notes.jsonl", "search_results.jsonl"]
KINDS = ["dates_long", "dates_us", "dates_slash_unamb", "dates_dotted", "dates_two_digit",
         "dates_us_slash", "dates_year_only", "name_order", "diacritics_add", "diacritics_strip",
         "transliterate", "country_alpha2", "country_alpha3", "country_upper", "null_non_identity",
         "case_whitespace", "extra_fields", "renamed_fields", "list_vs_string", "junk_lines", "combined"]
MODES = ("combined", "check")
COUNTRIES = json.loads((CORE_DIR / "countries.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- io

def read(folder: Path, name: str) -> list[dict]:
    p = folder / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write(folder: Path, name: str, rows: list[dict], junk: bool = False) -> None:
    with (folder / name).open("w", encoding="utf-8", newline="\n") as fh:
        if junk:
            fh.write("﻿")
        for i, r in enumerate(rows):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if junk and i % 97 == 0:
                fh.write("{this is not json\n[1,2,3]\n\n")


# --------------------------------------------------------------------------- perturbations
# (copied from contrib/aml-bot/engine/stress_test.py; keep in step with it)

def iso_parts(v):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(v).strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def refmt(v, style):
    p = iso_parts(v)
    if not p:
        return v
    y, m, d = p
    return {"long": f"{d} {MONTH[m - 1]} {y}", "us": f"{MONTH[m - 1]} {d}, {y}",
            "slash_unamb": f"{y}/{m:02d}/{d:02d}",
            "dotted": f"{d:02d}.{m:02d}.{y}" if d > 12 else f"{y}.{m:02d}.{d:02d}",
            "two_digit": f"{d:02d}/{m:02d}/{y % 100:02d}", "us_slash": f"{m:02d}/{d:02d}/{y}",
            "year_only": f"{y}"}[style]


def map_dates(tables, style):
    for name, flds in DATE_FIELDS.items():
        for r in tables.get(name, []):
            for f in flds:
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
    if not isinstance(n, str):
        return n
    return "".join(ACCENT.get(ch, ch) if i % 3 == 0 else ch for i, ch in enumerate(n))


def strip_accents(n):
    if not isinstance(n, str):
        return n
    return "".join(c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c))


def to_code(v, kind):
    cc = E.country_code(v) if isinstance(v, str) else None
    if not cc or cc == E.UNKNOWN_COUNTRY:
        return v
    aliases = COUNTRIES.get(cc, [])
    return {"alpha2": cc,
            "alpha3": next((a for a in aliases if len(a) == 3 and a.isupper()), cc),
            "upper": (E.country_name(cc) or v).upper()}[kind]


def perturb(tables, kind, rng, variants):  # noqa: C901 - one branch per perturbation, as in contrib
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
                    if f in r:
                        r[f] = strip_accents(r[f])
    elif kind == "transliterate":
        for h in tables["hits.jsonl"]:
            new = []
            for p in (h.get("name") or "").split():
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
                      ("alerts.jsonl", ("customer_name", "country")),
                      ("kyc_checks.jsonl", ("verified_name",))):
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
        for k in ("dates_long", "name_order", "diacritics_add", "country_alpha3", "extra_fields",
                  "list_vs_string"):
            perturb(tables, k, rng, variants)
        junk = True
    else:
        raise ValueError(f"unknown perturbation {kind}")
    return junk


def make_copy(src: Path, dst: Path, kind: str, base_tables: dict, variants: dict) -> None:
    dst.mkdir(parents=True)
    tables = json.loads(json.dumps(base_tables))
    junk = perturb(tables, kind, random.Random(7), variants)
    for t, rows in tables.items():
        write(dst, t, rows, junk=junk)
    for extra in sorted(os.listdir(src)):
        if extra not in TABLES and (src / extra).is_file():
            shutil.copy(src / extra, dst / extra)


# --------------------------------------------------------------------------- running

def run_system(folder: Path, out: Path, mode: str) -> dict:
    env = dict(os.environ, SCREEN_ENGINE=mode, PYTHONHASHSEED="0")
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, str(SCREEN_PY), "run", str(folder), str(out), "--quiet"],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows: dict[str, dict] = {}
    if proc.returncode == 0 and out.exists():
        with out.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    rows[r["hit_id"]] = r
    err = None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        err = f"exit {proc.returncode}: {tail[0][:200]}"
    return {"rows": rows, "error": err, "seconds": round(time.perf_counter() - t0, 1)}


def compare(base: dict, new: dict) -> dict:
    flips: Counter = Counter()
    unsafe = []
    for hid in sorted(base):
        b = base[hid]
        n = new.get(hid)
        nd = n["decision"] if n else "MISSING"
        if nd != b["decision"]:
            flips[f"{b['decision']}->{nd}"] += 1
            if nd == "DIFFERENT_ENTITY":
                unsafe.append({"hit_id": hid, "before": f"{b['decision']}/{b['reason_code']}",
                               "after": n["reason_code"]})
    return {"flips": dict(sorted(flips.items())), "unsafe": unsafe}


def markdown(report: dict, modes: list[str], kinds: list[str]) -> str:
    head = "| perturbation | " + " | ".join(f"{m} unsafe" for m in modes) + " | combined flips | unsafe hits (combined) |"
    lines = [head, "|" + "---|" * (len(modes) + 3)]
    for k in kinds:
        cells = []
        for m in modes:
            r = report[m][k]
            cells.append("CRASH" if r["error"] else str(len(r["unsafe"])))
        c = report.get("combined", {}).get(k)
        flips = ", ".join(f"{a} {b}" for a, b in c["flips"].items()) if c else ""
        hits = ", ".join(f"{u['hit_id']} ({u['before']} -> {u['after']})" for u in c["unsafe"]) if c else ""
        lines.append(f"| {k} | " + " | ".join(cells) + f" | {flips or '-'} | {hits or '-'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data")
    ap.add_argument("--kinds", nargs="+", default=KINDS, choices=KINDS)
    ap.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES)
    ap.add_argument("--workers", type=int, default=4, help="parallel processes (default 4)")
    ap.add_argument("--report", help="write the JSON report here (default: system temp dir)")
    ap.add_argument("--markdown", action="store_true", help="also print a markdown flip table")
    args = ap.parse_args(argv)

    src = Path(args.data).resolve()
    work = Path(tempfile.mkdtemp(prefix="stress_system_"))  # outside the repo
    try:
        base_tables = {t: read(src, t) for t in TABLES}
        nv_path = src / "name_variants.json"
        nv = json.loads(nv_path.read_text(encoding="utf-8")) if nv_path.exists() else {}
        variants = {n.lower(): set(g) for g in nv.get("given_name_groups", []) for n in g}
        for k in args.kinds:
            make_copy(src, work / k, k, base_tables, variants)

        jobs = [("__base__", m) for m in args.modes] + [(k, m) for k in args.kinds for m in args.modes]

        def job(km):
            k, m = km
            folder = src if k == "__base__" else work / k
            return km, run_system(folder, work / f"out_{k}_{m}.jsonl", m)

        results: dict = {}
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for (k, m), res in pool.map(job, jobs):
                results[(k, m)] = res
                print(f"  ran {k:<18} {m:<9} {res['seconds']:>6}s{'  ' + res['error'] if res['error'] else ''}",
                      file=sys.stderr, flush=True)

        report: dict = {}
        bad_combined = 0
        for m in args.modes:
            base = results[("__base__", m)]
            if base["error"]:
                print(f"baseline {m} failed: {base['error']}", file=sys.stderr)
                return 2
            cleared = sum(r["decision"] == "DIFFERENT_ENTITY" for r in base["rows"].values())
            print(f"[{m}] baseline: {len(base['rows'])} hits, {cleared} cleared")
            report[m] = {}
            total = 0
            for k in args.kinds:
                res = results[(k, m)]
                cmp = compare(base["rows"], res["rows"])
                cmp["error"] = res["error"]
                report[m][k] = cmp
                n = len(cmp["unsafe"])
                total += n
                if m == "combined":
                    bad_combined += n + (1 if res["error"] else 0)
                eg = ", ".join(f"{u['hit_id']}->{u['after']}" for u in cmp["unsafe"][:6])
                status = "CRASH " + res["error"] if res["error"] else f"unsafe={n:3d}"
                print(f"  {k:20s} {status}  flips={cmp['flips']}" + (f"  e.g. {eg}" if eg else ""))
            print(f"[{m}] TOTAL UNSAFE FLIPS: {total}")

        out = Path(args.report) if args.report else Path(tempfile.gettempdir()) / "stress_system_report.json"
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {out}")
        if args.markdown:
            print()
            print(markdown(report, args.modes, args.kinds))
        return 1 if bad_combined else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
