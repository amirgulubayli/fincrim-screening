"""The ONE standard scorecard: every headline number, computed from the engine itself.

    python tools/scorecard.py --data data/dev            # runs the engine + a fresh 5-run repro
    python tools/scorecard.py --data data/dev --runs 0   # skip repro (fast, ~1.5 min)

Writes docs/RESULTS.md (human) and docs/results.json (machine). Aggregates only: no names,
no record contents, so both files are safe in the public repo. Quote numbers ONLY from here.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system"))

from screening import __version__  # noqa: E402
from screening.contracts import REASONS_BY_DECISION  # noqa: E402
from screening.evaluate import score  # noqa: E402
from screening.loaders import load_dataset  # noqa: E402
from screening.pipeline import run  # noqa: E402

RULE_MODULES = sorted(p.stem for p in (ROOT / "system/screening/rules").glob("*.py")
                      if p.stem not in {"__init__", "base"})


def review_reason(d) -> str:
    s = d.reason.lower()
    if d.reason_code.value == "AMBIGUOUS_CUSTOMER_RECORD":
        return "several customers fit and lead to different outcomes"
    if "ENGINES_CONFLICT" in d.flags:
        return "engines conflict (core clears, check confirms)"
    if s.startswith("escalated, not cleared"):
        return "cautious policy call (Head of FinCrime)"
    if "no customer record" in s or "did not lead" in s:
        return "no customer record found"
    if "date of birth" in s:
        return "name and country agree but no usable hit DOB (SOP requires review)"
    return "other insufficient data"


def pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev")
    ap.add_argument("--runs", type=int, default=5, help="fresh-process repro runs (0 = skip)")
    ap.add_argument("--out-md", default="docs/RESULTS.md")
    ap.add_argument("--out-json", default="docs/results.json")
    args = ap.parse_args()

    t0 = time.perf_counter()
    ds = load_dataset(args.data)
    t_load = time.perf_counter() - t0
    decisions = run(ds)
    t_run = time.perf_counter() - t0
    subs = [d.submission() for d in decisions]
    ev = score(subs, ds.practice_labels) if ds.practice_labels else {}
    ev.pop("rows", None)

    n = len(decisions)
    dec = Counter(d.decision.value for d in decisions)
    tiers = Counter(d.tier.value for d in decisions)
    conflict = sum("ENGINES_CONFLICT" in d.flags for d in decisions)
    disagree = sum("ENGINES_DISAGREE" in d.flags for d in decisions)
    policy_esc = sum("ESCALATED_BY_POLICY" in d.flags for d in decisions)
    reviews = Counter(review_reason(d) for d in decisions if d.decision.value == "REVIEW")
    auto = tiers["AUTO_CLEAR"] + tiers["AUTO_ESCALATE"]
    alerts_closed = sum(
        1 for a in ds.alerts.values()
        if a.hit_ids and all(next((d for d in decisions if d.hit_id == h), None) is not None
                             and next(d for d in decisions if d.hit_id == h).decision.value == "DIFFERENT_ENTITY"
                             for h in a.hit_ids if h in ds.hits)
    )

    try:
        from screening.policy import registry
        pol = Counter(p["kind"] for p in registry())
    except Exception:  # noqa: BLE001
        pol = Counter()

    repro = None
    if args.runs:
        out = subprocess.run([sys.executable, str(ROOT / "system/screen.py"), "repro", "--data", args.data,
                              "--runs", str(args.runs), "--out-dir", "output/repro", "--json"],
                             capture_output=True, text=True, encoding="utf-8")
        try:
            r = json.loads(out.stdout[out.stdout.index("{"):])
            repro = {"runs": len(r["runs"]), "byte_identical": r["byte_identical"],
                     "decision_reproducibility": r["decision_reproducibility"],
                     "sha256": r["runs"][0]["sha256"], "seconds_per_run": [x["seconds"] for x in r["runs"]]}
        except (ValueError, KeyError):
            repro = {"error": (out.stderr or out.stdout)[-500:]}

    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "engine_version": __version__,
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "dataset": ds.summary(),
        "practice_labels": {k: ev.get(k) for k in (
            "labelled_hits", "decision_score", "decision_accuracy", "unsafe_clearances", "review_rate",
            "reason_code_accuracy_on_correct", "customer_id_accuracy_on_correct",
            "evidence_precision", "evidence_recall", "confusion")},
        "all_hits": {
            "hits": n, "decisions": dict(dec), "tiers": dict(tiers),
            "fully_automated_pct": pct(auto, n),
            "decided_by_engine_pct": pct(dec["DIFFERENT_ENTITY"] + dec["SAME_ENTITY"], n),
            "cleared_pct": pct(dec["DIFFERENT_ENTITY"], n),
            "alerts_fully_cleared": alerts_closed, "alerts": len(ds.alerts),
            "engine_agreement_pct": pct(n - disagree - conflict, n),
            "engines_disagree": disagree, "engines_conflict": conflict,
            "escalated_by_policy": policy_esc,
            "review_reasons": dict(reviews.most_common()),
        },
        "performance": {"load_seconds": round(t_load, 1), "run_seconds": round(t_run, 1),
                        "api_cost": "0 (no model calls)"},
        "reproducibility": repro,
        "solution": {
            "decision_engines": 2,
            "core": "AML Bot reference engine (teammate, PR #28): 8-step rule order + independent second_check",
            "check_rule_modules": RULE_MODULES,
            "reason_codes": sum(len(v) for v in REASONS_BY_DECISION.values()),
            "policies": dict(pol), "policies_total": sum(pol.values()),
            "llm_in_decision_path": False,
            "standard_library_only": True,
        },
    }
    Path(args.out_json).write_text(json.dumps(data, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render(data), encoding="utf-8")
    print(render(data))
    return 0


def render(d: dict) -> str:
    pl, ah, so, pf, rp = (d["practice_labels"], d["all_hits"], d["solution"], d["performance"],
                          d["reproducibility"])
    t = ah["tiers"]
    lines = [
        "# Results (standard scorecard)",
        "",
        f"Generated {d['generated']} by `python tools/scorecard.py` (engine {d['engine_version']}, "
        f"{d['machine']}). **Quote numbers only from this file.** Dev set: {ah['hits']} hits, "
        f"{ah['alerts']} alerts, {d['dataset']['customers']:,} customer records.",
        "",
        "## 1. Accuracy on the 50 practice labels (the organisers' cost model)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Decision score (share of cost saved vs. all-REVIEW) | **{pl['decision_score']}** |",
        f"| Correct decisions | {round(pl['decision_accuracy'] * pl['labelled_hits'])} / {pl['labelled_hits']} |",
        f"| Unsafe clearances (true match cleared, cost 60 each) | **{pl['unsafe_clearances']}** |",
        f"| Reason code right (when decision right) | {pl['reason_code_accuracy_on_correct']:.0%} |",
        f"| Customer record right (when decision right) | {pl['customer_id_accuracy_on_correct']:.0%} |",
        f"| Evidence precision / recall | {pl['evidence_precision']} / {pl['evidence_recall']} |",
        f"| Review rate | {pl['review_rate']:.0%} (true REVIEW labels: 20%) |",
        "",
        "## 2. All dev hits: what is automated, what needs a human",
        "",
        "| Tier | Hits | Meaning |",
        "|---|---|---|",
        f"| Auto-cleared | {t.get('AUTO_CLEAR', 0)} | hard contradiction, both engines agree |",
        f"| Auto-escalated | {t.get('AUTO_ESCALATE', 0)} | confirmed match -> compliance team |",
        f"| Borderline (4-eyes) | {t.get('BORDERLINE', 0)} | decided, but engines disagree or a fact is weak |",
        f"| Human review | {t.get('HUMAN_REVIEW', 0)} | SOP says REVIEW |",
        "",
        f"- Fully automated: **{ah['fully_automated_pct']}%**; decided by the engine (cleared or confirmed): "
        f"**{ah['decided_by_engine_pct']}%**; cleared: {ah['cleared_pct']}%; alerts fully closed: "
        f"{ah['alerts_fully_cleared']} / {ah['alerts']}.",
        f"- Two engines agree on **{ah['engine_agreement_pct']}%** of hits ({ah['engines_disagree']} disagree, "
        f"{ah['engines_conflict']} conflict).",
        "- Why hits go to review:",
        *[f"  - {k}: {v}" for k, v in ah["review_reasons"].items()],
        "",
        "## 3. Reproducibility and cost",
        "",
    ]
    if rp and "byte_identical" in rp:
        lines.append(f"- {rp['runs']} fresh runs (different PYTHONHASHSEED): byte-identical = "
                     f"**{rp['byte_identical']}**, decision reproducibility {rp['decision_reproducibility']:.0%}, "
                     f"sha256 `{rp['sha256'][:16]}...`")
    else:
        lines.append(f"- Repro: {rp}")
    lines += [
        f"- One run: {pf['run_seconds']} s (of which data load {pf['load_seconds']} s); limit is 30 min. "
        f"API cost: {pf['api_cost']}.",
        "",
        "## 4. The solution in numbers",
        "",
        f"- {so['decision_engines']} independent deterministic engines (dual control): core = {so['core']}; "
        f"check = {len(so['check_rule_modules'])} rule modules ({', '.join(so['check_rule_modules'])}) + a "
        "fixed-precedence compiler.",
        f"- {so['policies_total']} policies in the registry ({', '.join(f'{v} {k}' for k, v in so['policies'].items())}); "
        f"{so['reason_codes']} reason codes.",
        f"- LLM in the decision path: **{so['llm_in_decision_path']}**. Python standard library only: "
        f"{so['standard_library_only']}.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
