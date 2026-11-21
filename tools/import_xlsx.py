"""STOPGAP: rebuild data/dev/*.jsonl from the browse workbook.

The data room's dev/ JSONL files are the source of truth (the unseen set uses
that format). Use this only until you have them. Known limits of the workbook:
long texts are cut at 32,000 characters, multi-valued fields are "a; b" strings,
and name_variants.json is reconstructed (its real layout may differ).

    uv run --with openpyxl python tools/import_xlsx.py "path/to/Screening Dev Set - browse.xlsx"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHEETS = {
    "Alerts": "alerts.jsonl",
    "Hits": "hits.jsonl",
    "Customers": "customers.jsonl",
    "Accounts": "accounts.jsonl",
    "Onboarding": "onboarding.jsonl",
    "KYC checks": "kyc_checks.jsonl",
    "Notes": "notes.jsonl",
    "Search results": "search_results.jsonl",
    "Practice labels": "practice_labels.jsonl",
}


def _cell(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+\.0", value):
        return int(float(value))
    return value


def export_policy(ws, out: Path) -> None:
    lines = [str(c) for row in ws.iter_rows(values_only=True) for c in row if c is not None]
    split = next(i for i, line in enumerate(lines) if line.strip() == "name_variants.json")
    policy, variants = lines[:split], lines[split + 1 :]
    md: list[str] = []
    for line in policy:
        tight = line.lstrip().startswith(("|", "-", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
        md.append(("\n" if tight and md else "\n\n") + line if md else line)
    (out / "POLICY.md").write_text("".join(md) + "\n", encoding="utf-8")
    groups, legal = [], []
    for line in variants[1:]:
        if line.lower().startswith("company legal forms:"):
            legal = [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
        elif "," in line:
            groups.append([p.strip() for p in line.split(",") if p.strip()])
    data = {"_note": "reconstructed from the workbook", "given_names": groups,
            "company_legal_forms": legal}
    (out / "name_variants.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    import openpyxl  # dev-only dependency; system/ never imports it

    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out", default="data/dev")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    for sheet, filename in SHEETS.items():
        rows = wb[sheet].iter_rows(values_only=True)
        header = [str(h) for h in next(rows)]
        n = 0
        with (out / filename).open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                if all(v is None for v in row):
                    continue
                rec = {h: _cell(v) for h, v in zip(header, row)}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
        print(f"{filename:<24} {n:>7} rows")
    export_policy(wb["Policy"], out)
    print("POLICY.md, name_variants.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
