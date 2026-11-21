"""Baseline: Grok ALONE decides each practice-labelled hit (no CLI, no rules).

For each labelled hit it gives Grok the SOP, the hit, the alert and the linked customer
records, asks for {decision, reason_code, customer_record_id, evidence}, and caches the
answer in out/grok_baseline/<hit>.json (re-runs reuse the cache). Output is scored with
the same cost matrix as the engine:

    grok login            # once (or set XAI_API_KEY)
    python tools/grok_baseline.py --data data/dev
    python tools/benchmark.py --data data/dev     # rebuilds the side-by-side page

Never used in system/: this exists only to measure what our CLI adds over an LLM alone.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system"))

from screening.loaders import load_dataset  # noqa: E402

PROMPT = """You are an AML analyst. Decide whether the watchlist hit is the bank's customer, following the SOP.
Answer ONLY one JSON object: {{"decision": "DIFFERENT_ENTITY|SAME_ENTITY|REVIEW", "reason_code": "<one code from the SOP table>",
"customer_record_id": "<CUS-id, AMBIGUOUS or null>", "evidence": ["record ids that decided it"]}}

SOP:
{policy}

ALERT: {alert}
HIT: {hit}
CANDIDATE CUSTOMER RECORDS (from the alert's link): {customers}
"""


def candidates(ds, alert) -> list[dict]:
    ids: set[str] = set()
    if alert.account_ref:
        ids |= {a.customer_id for a in ds.accounts_by_id.get(alert.account_ref, [])}
    if alert.login_handle:
        ids |= {a.customer_id for a in ds.accounts_by_login.get(alert.login_handle.lower(), [])}
    if alert.email:
        ids |= {c.record_id for c in ds.customers_by_email.get(alert.email.lower(), [])}
    out = []
    for cid in sorted(ids)[:5]:
        c = ds.customers.get(cid)
        if c:
            notes = ds.notes_by_email.get((c.email or "").lower(), [])[:3]
            out.append({"record": {k: v for k, v in vars(c).items() if k != "extra"},
                        "notes": [{"note_id": n.note_id, "text": n.text} for n in notes]})
    return out


def ask_grok(prompt: str) -> dict | None:
    p = subprocess.run(["grok", "-p", prompt, "--output-format", "plain", "--no-subagents",
                        "--disable-web-search", "--max-turns", "1"],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    m = re.search(r"\{.*\}", p.stdout, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev")
    ap.add_argument("--out", default="out/grok_baseline")
    args = ap.parse_args()
    if not shutil.which("grok"):
        print("grok CLI not found", file=sys.stderr)
        return 2
    ds = load_dataset(args.data)
    cache = Path(args.out)
    cache.mkdir(parents=True, exist_ok=True)
    preds = []
    for hid in sorted(ds.practice_labels):
        f = cache / f"{hid}.json"
        if f.exists():
            preds.append(json.loads(f.read_text(encoding="utf-8")))
            continue
        hit = ds.hits[hid]
        alert = ds.alerts[hit.alert_id]
        prompt = PROMPT.format(policy=ds.policy_text, alert=json.dumps(vars(alert), default=str),
                               hit=json.dumps({k: v for k, v in vars(hit).items() if k != "extra"}, default=str),
                               customers=json.dumps(candidates(ds, alert), default=str))
        ans = ask_grok(prompt) or {"decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA",
                                   "customer_record_id": None, "evidence": [], "_unparsed": True}
        ans.update(hit_id=hid, alert_id=hit.alert_id)
        f.write_text(json.dumps(ans), encoding="utf-8")
        preds.append(ans)
        print(hid, ans.get("decision"), file=sys.stderr)
    with (cache.parent / "grok_baseline.jsonl").open("w", encoding="utf-8") as fh:
        for p in preds:
            fh.write(json.dumps(p) + "\n")
    print(f"wrote {len(preds)} predictions to {cache.parent / 'grok_baseline.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
