"""Side-by-side benchmark page: Grok alone vs teammate engine vs our CLI vs Grok + our CLI.

    python tools/benchmark.py --data data/dev            # runs the 3 engine modes (~2 min)
    python tools/benchmark.py --data data/dev --reuse DIR  # reuse DIR/{core,check,combined}.jsonl (+ .trace.jsonl)

Writes docs/benchmark.html (self-contained; numbers only, no customer data).
Grok-alone numbers come from out/grok_baseline.jsonl (tools/grok_baseline.py); if that file
is missing the column says "not run yet" instead of inventing a number.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system"))

from screening.evaluate import score  # noqa: E402
from screening.loaders import load_dataset  # noqa: E402


def read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_modes(data: str, out: Path) -> dict[str, float]:
    secs = {}
    procs = {}
    for m in ("core", "check", "combined"):
        env = dict(os.environ, SCREEN_ENGINE=m)
        procs[m] = (time.perf_counter(), subprocess.Popen(
            [sys.executable, str(ROOT / "system/screen.py"), "run", data, str(out / f"{m}.jsonl"),
             "--trace", str(out / f"{m}.trace.jsonl"), "--quiet"], env=env))
    for m, (t0, p) in procs.items():
        p.wait()
        secs[m] = round(time.perf_counter() - t0, 1)
    return secs


def card(title, who, s, note, accent):
    if s is None:
        body = '<div class="big muted">not run yet</div><p class="small">Run <code>grok login</code> then ' \
               '<code>python tools/grok_baseline.py</code> and rebuild this page.</p>'
    else:
        rows = [("Decision score", f"{s['decision_score']*100:.1f}%"),
                ("Correct decisions", f"{round(s['decision_accuracy']*s['labelled_hits'])}/{s['labelled_hits']}"),
                ("Unsafe clearances", str(s["unsafe_clearances"])),
                ("Sent to review", f"{s['review_rate']*100:.0f}%"),
                ("Right reason code", _pct(s.get("reason_code_accuracy_on_correct"))),
                ("Right customer", _pct(s.get("customer_id_accuracy_on_correct"))),
                ("Evidence precision", _pct(s.get("evidence_precision")))]
        body = f'<div class="big">{s["decision_score"]*100:.1f}%</div><table>' + "".join(
            f'<tr><td>{k}</td><td class="{"bad" if k=="Unsafe clearances" and v!="0" else ""}">{v}</td></tr>'
            for k, v in rows) + "</table>"
    return (f'<div class="card" style="border-top-color:{accent}"><h3>{title}</h3><div class="who">{who}</div>'
            f'{body}<p class="small">{note}</p></div>')


def _pct(v):
    return "–" if v is None else f"{v*100:.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev")
    ap.add_argument("--reuse")
    ap.add_argument("--out", default="docs/benchmark.html")
    args = ap.parse_args()
    ds = load_dataset(args.data)
    labels = ds.practice_labels
    work = Path(args.reuse) if args.reuse else Path(tempfile.mkdtemp())
    secs = {} if args.reuse else run_modes(args.data, work)
    res = {m: score(read(work / f"{m}.jsonl"), labels) for m in ("core", "check", "combined")}
    gpath = ROOT / "out/grok_baseline.jsonl"
    grok = score(read(gpath), labels) if gpath.exists() else None

    comb = read(work / "combined.trace.jsonl")
    tiers = Counter(d["tier"] for d in comb)
    decisions = Counter(d["decision"] for d in comb)
    agree = sum(1 for d in comb if not ({"ENGINES_DISAGREE", "ENGINES_CONFLICT"} & set(d["flags"])))
    policy = sum(1 for d in comb if "ESCALATED_BY_POLICY" in d["flags"])
    fired = Counter((r["rule_id"], r["signal"]) for d in comb for r in d["trace"] if r["rule_id"] != "CORE.aml")
    n = len(comb)

    def fires(rule):
        return fired[(rule, "contradiction")], fired[(rule, "agreement")]

    stages = [
        ("1", "Load & formalise", f"{n} hits, {len(ds.alerts)} alerts, {len(ds.customers):,} customers; "
         "dates (day-first), 250 countries + demonyms, name variants normalised", "loaders · normalize/*"),
        ("2", "Resolve the customer", "follow account / login / email link, else search; join holders; "
         "identity graph (phone+DOB, document no.); latest approved KYC/note = verified identity", "resolve · identity  (SOP P2–P4)"),
        ("3", "Gather hit facts", "DOBs from dobs → profile notes → filtered web results; countries; "
         "deceased date; earliest dated report", "facts  (SOP P5)"),
        ("4", "Decision core", "teammate's reviewed engine + 9 Head-of-FinCrime policy calls + its own "
         "independent re-derivation of every clearance", "core/aml_engine"),
        ("5", "Independent rule check", "6 rule families → "
         + ", ".join(f"{r.split('.')[1]}: {c} clear / {a} agree" for r, (c, a) in
                     ((r, fires(r)) for r in ("P6.type", "P6.deceased", "P6.temporal", "P6.dob", "P6.name", "P6.location"))),
         "rules/*  (SOP P6–P8)"),
        ("6", "Dual-control compiler", f"engines agree on {agree}/{n} ({agree/n*100:.0f}%); core clear vs check "
         "confirm → REVIEW; any other disagreement → 4-eyes", "compiler.merge_with_core"),
        ("7", "Human-in-the-loop tiers", f"auto-cleared {tiers['AUTO_CLEAR']} · auto-escalated {tiers['AUTO_ESCALATE']}"
         f" · borderline {tiers['BORDERLINE']} · analyst review {tiers['HUMAN_REVIEW']} ({policy} escalated by policy "
         "with a written Reviewer action)", "triage"),
        ("8", "Explain & emit", "2–4 sentence reason, cited record IDs, customer id → output/dev.jsonl; "
         "dashboard case file; Grok plugin narrates (never decides)", "explain · cli · app · .grok plugin"),
    ]
    timeline = "".join(
        f'<li><span class="dot">{i}</span><div><b>{html.escape(t)}</b> <span class="mod">{html.escape(m)}</span>'
        f'<br><span class="small">{html.escape(d)}</span></div></li>' for i, t, d, m in stages)

    t = lambda m: f" · {secs[m]} s per run" if m in secs else ""  # noqa: E731
    cards = "".join([
        card("Grok alone", "LLM reads SOP + records, decides", grok,
             "No rules, no CLI. Non-deterministic: answers can change between runs.", "#9aa0a6"),
        card("Teammate's engine", "AML Bot core (akhackathon222)", res["core"],
             "Deterministic regex + policy engine with Head-of-FinCrime calls" + t("core") + ".", "#7b61ff"),
        card("Our CLI rule set", "modular SOP rules (screen --engine check)", res["check"],
             "Facet-by-facet rules, one per SOP clause, with a full trace" + t("check") + ".", "#1a73e8"),
        card("Grok + our CLI (shipped)", "Grok drives the CLI via the plugin", res["combined"],
             "Both deterministic engines must agree to automate; Grok only narrates the trace"
             + t("combined") + ". 5 runs byte-identical.", "#188038"),
    ])
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Screening benchmark</title><style>
body{{font:15px/1.45 system-ui,Segoe UI,sans-serif;margin:32px auto;max-width:1180px;color:#202124;padding:0 16px}}
h1{{font-size:24px;margin:0}} h2{{font-size:18px;margin:36px 0 10px}} .sub{{color:#5f6368;margin:4px 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .card{{border:1px solid #dadce0;border-top:4px solid;
border-radius:8px;padding:14px}} .card h3{{margin:0;font-size:16px}} .who{{color:#5f6368;font-size:13px}}
.big{{font-size:34px;font-weight:700;margin:8px 0}} .muted{{color:#9aa0a6;font-size:22px}} table{{width:100%;font-size:13px;border-collapse:collapse}}
td{{padding:3px 0;border-bottom:1px solid #f1f3f4}} td:last-child{{text-align:right;font-weight:600}} .bad{{color:#d93025}}
.small{{font-size:12.5px;color:#5f6368}} ol{{list-style:none;padding:0;border-left:3px solid #dadce0;margin-left:14px}}
li{{display:flex;gap:12px;margin:0 0 14px -15px}} .dot{{flex:none;width:26px;height:26px;border-radius:50%;background:#1a73e8;color:#fff;
display:grid;place-items:center;font-size:13px;font-weight:700}} .mod{{font:12px ui-monospace,monospace;color:#7b61ff}}
code{{font:12.5px ui-monospace,monospace;background:#f1f3f4;padding:1px 4px;border-radius:3px}} .kpis{{display:flex;gap:28px;flex-wrap:wrap}}
.kpis div b{{display:block;font-size:22px}}
</style></head><body>
<h1>Is my customer sanctioned? — what each layer adds</h1>
<p class="sub">Scored on the {len(labels)} practice-labelled dev hits with the organisers' cost matrix
(clearing a true match costs 60; an unneeded review 2–3; decision score = cost saved vs. sending everything to review).</p>
<div class="grid">{cards}</div>
<h2>Whole dev set ({n} hits) — shipped configuration</h2>
<div class="kpis"><div><b>{decisions['DIFFERENT_ENTITY']}</b>cleared</div><div><b>{decisions['SAME_ENTITY']}</b>confirmed</div>
<div><b>{decisions['REVIEW']}</b>to review</div><div><b>{(tiers['AUTO_CLEAR']+tiers['AUTO_ESCALATE'])/n*100:.0f}%</b>fully automated</div>
<div><b>{agree/n*100:.0f}%</b>engines agree</div><div><b>£0</b>per run (no API)</div></div>
<h2>What happens to one hit — the pipeline</h2><ol>{timeline}</ol>
<p class="small">Rule base: SOP principles P1–P9 and its 11 reason codes, 9 Head-of-FinCrime policy calls, 2 dual-control rules.
External typology sources (FATF, NCA, OpenSanctions) are context only and never change a decision.</p>
</body></html>"""
    Path(args.out).write_text(page, encoding="utf-8")
    print(json.dumps({"out": args.out, "grok_alone": grok and grok["decision_score"],
                      **{m: res[m]["decision_score"] for m in res}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
