#!/usr/bin/env python3
"""Error-analysis ("fit") report: where does the engine disagree with the practice labels,
and which decisions on unlabelled hits look like candidate unsafe clearances?

Standard library only. Imports the engine from system/ and runs the pipeline once.

    uv run python tools/error_report.py --data data/dev
    uv run python tools/error_report.py --data data/dev --out out/error_report.md --json

Sections:
  1. Score summary (screening.evaluate.score).
  2. Per-label diffs grouped by failure type (unsafe clearance first), with the label's
     path / reference_reason and our rule trace, so you see which step diverged.
  3. Risk scan over all hits: decisions by tier, and DIFFERENT_ENTITY decisions on
     unlabelled hits that rest on a location / name / DOB contradiction while other
     signals suggest danger.
  4. Stage diagnostics: customer resolution by method, hit DOB candidates by source,
     countries on both sides.

Written defensively (getattr / try-except) so it keeps working while the engine changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))

from screening.evaluate import score  # noqa: E402
from screening.loaders import load_dataset  # noqa: E402
from screening.pipeline import run  # noqa: E402

FAILURE_ORDER = (
    ("unsafe_clearance", "Unsafe clearance (truth SAME/REVIEW, we DIFFERENT)"),
    ("missed_clear", "Missed clear (truth DIFFERENT, we REVIEW)"),
    ("missed_confirm", "Missed confirm (truth SAME, we REVIEW)"),
    ("wrong_decision_other", "Other wrong decision"),
    ("wrong_reason", "Wrong reason code (decision correct)"),
    ("wrong_customer", "Wrong customer (decision correct)"),
    ("evidence_mismatch", "Evidence mismatch"),
)
RISKY_REASONS = ("LOCATION_MISMATCH", "NAME_MISMATCH_VERIFIED", "DOB_MISMATCH")
SUMMARY_MAX = 240


# ------------------------------------------------------------------ helpers

def _v(x: Any) -> Any:
    """Enum -> value, anything else unchanged."""
    return getattr(x, "value", x)


def _short(text: Any, limit: int = SUMMARY_MAX) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _md(text: Any) -> str:
    return _short(text).replace("|", "\\|")


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "n/a"


def _trace_rows(dec: Any) -> list[dict[str, Any]]:
    rows = []
    for r in getattr(dec, "trace", ()) or ():
        try:
            rows.append({
                "rule_id": getattr(r, "rule_id", "?"),
                "facet": _v(getattr(r, "facet", None)),
                "signal": _v(getattr(r, "signal", None)),
                "reason": _v(getattr(r, "reason", None)),
                "summary": getattr(r, "summary", ""),
                "evidence": list(getattr(r, "evidence", ()) or ()),
                "weak": bool(getattr(r, "weak", False)),
                "weak_why": getattr(r, "weak_why", None),
            })
        except Exception:  # noqa: BLE001 - never let one odd row break the report
            rows.append({"rule_id": "?", "signal": "?", "summary": repr(r)})
    return rows


def _cid_ok(pred: str | None, label: Any) -> bool:
    try:
        from screening.evaluate import _cid_ok as ok  # private helper; fall back if it moves

        return bool(ok(pred, label))
    except Exception:  # noqa: BLE001
        accepted = {str(c).upper() for c in getattr(label, "customer_record_ids", ()) or ()}
        if not accepted or accepted <= {"NULL", "NONE"}:
            return pred is None
        return (pred or "").upper() in accepted


def _safe(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ analysis

def classify(truth: str, pred: dict[str, Any], label: Any) -> list[str]:
    p_dec = pred.get("decision", "MISSING")
    kinds: list[str] = []
    if truth != "DIFFERENT_ENTITY" and p_dec == "DIFFERENT_ENTITY":
        kinds.append("unsafe_clearance")
    elif truth == "DIFFERENT_ENTITY" and p_dec == "REVIEW":
        kinds.append("missed_clear")
    elif truth == "SAME_ENTITY" and p_dec == "REVIEW":
        kinds.append("missed_confirm")
    elif truth != p_dec:
        kinds.append("wrong_decision_other")
    else:
        if pred.get("reason_code") not in set(getattr(label, "reason_codes", ()) or ()):
            kinds.append("wrong_reason")
        if not _cid_ok(pred.get("customer_record_id"), label):
            kinds.append("wrong_customer")
    if set(pred.get("evidence", []) or []) != set(getattr(label, "evidence", ()) or ()):
        kinds.append("evidence_mismatch")
    return kinds


def label_diffs(decisions: dict[str, Any], labels: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for hit_id in sorted(labels):
        label = labels[hit_id]
        dec = decisions.get(hit_id)
        pred = dec.submission() if dec is not None else {"hit_id": hit_id, "decision": "MISSING"}
        truth = getattr(label, "decision", None) or "REVIEW"
        kinds = classify(truth, pred, label)
        if not kinds:
            continue
        pe, te = set(pred.get("evidence", []) or []), set(getattr(label, "evidence", ()) or ())
        out.append({
            "hit_id": hit_id,
            "alert_id": getattr(label, "alert_id", ""),
            "kinds": kinds,
            "primary": kinds[0],
            "truth_decision": truth,
            "pred_decision": pred.get("decision"),
            "truth_reasons": list(getattr(label, "reason_codes", ()) or ()),
            "pred_reason": pred.get("reason_code"),
            "truth_customers": list(getattr(label, "customer_record_ids", ()) or ()),
            "pred_customer": pred.get("customer_record_id"),
            "truth_evidence": sorted(te),
            "pred_evidence": sorted(pe),
            "evidence_missing": sorted(te - pe),
            "evidence_extra": sorted(pe - te),
            "path": getattr(label, "path", None),
            "reference_reason": getattr(label, "reference_reason", None),
            "pred_reason_text": pred.get("reason"),
            "tier": _v(getattr(dec, "tier", None)),
            "flags": list(getattr(dec, "flags", ()) or ()),
            "trace": _trace_rows(dec) if dec is not None else [],
        })
    return out


def risk_scan(decisions: dict[str, Any], labels: dict[str, Any], resolutions: dict[str, Any]) -> dict[str, Any]:
    by_tier: Counter[str] = Counter()
    by_decision_tier: Counter[tuple[str, str]] = Counter()
    candidates = []
    for hit_id in sorted(decisions):
        dec = decisions[hit_id]
        d, tier = str(_v(dec.decision)), str(_v(getattr(dec, "tier", "?")))
        by_tier[tier] += 1
        by_decision_tier[(d, tier)] += 1
        reason = str(_v(dec.reason_code))
        if hit_id in labels or d != "DIFFERENT_ENTITY" or reason not in RISKY_REASONS:
            continue
        trace = _trace_rows(dec)
        agree = {t.get("facet") for t in trace if t.get("signal") == "agreement"}
        dangers = []
        if "name" in agree and "dob" in agree:
            dangers.append("NAME_AND_DOB_AGREE_IN_TRACE")
        elif reason == "DOB_MISMATCH" and "dob" in agree:
            dangers.append("DOB_AGREES_ELSEWHERE")
        weak = sorted({t.get("weak_why") or f"WEAK_{str(t.get('facet')).upper()}" for t in trace if t.get("weak")})
        flags = list(getattr(dec, "flags", ()) or ())
        if weak or [f for f in flags if f != "NO_CUSTOMER_RECORD"]:
            dangers.append("WEAK:" + ",".join(sorted(set(weak) | {f for f in flags if f != "NO_CUSTOMER_RECORD"})))
        res = resolutions.get(getattr(dec, "alert_id", ""))
        n_persons = len(getattr(res, "persons", ()) or ()) if res is not None else None
        if n_persons is not None and n_persons > 1:
            dangers.append(f"AMBIGUOUS_CUSTOMER({n_persons})")
        if "NO_CUSTOMER_RECORD" in flags or n_persons == 0 or getattr(dec, "customer_record_id", None) is None:
            dangers.append("NO_CUSTOMER_RECORD")
        if not dangers:
            continue
        deciding = [t for t in trace if t.get("signal") == "contradiction" and t.get("reason") == reason]
        candidates.append({
            "hit_id": hit_id,
            "alert_id": getattr(dec, "alert_id", ""),
            "reason_code": reason,
            "tier": tier,
            "customer_record_id": getattr(dec, "customer_record_id", None),
            "resolution_method": getattr(res, "method", None) if res is not None else None,
            "dangers": dangers,
            "deciding": [f"{t.get('rule_id')}: {t.get('summary')}" for t in deciding],
        })
    candidates.sort(key=lambda c: (-len(c["dangers"]), c["hit_id"]))
    return {
        "by_tier": dict(sorted(by_tier.items())),
        "by_decision_tier": {f"{d}/{t}": n for (d, t), n in sorted(by_decision_tier.items())},
        "unlabelled_hits": sum(1 for h in decisions if h not in labels),
        "candidates": candidates,
    }


def stage_diagnostics(ds: Any, resolutions: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    by_method: dict[str, Counter[str]] = defaultdict(Counter)
    for alert_id in sorted(resolutions):
        res = resolutions[alert_id]
        if res is None:
            by_method["error"]["alerts"] += 1
            continue
        n = len(getattr(res, "persons", ()) or ())
        m = by_method[str(getattr(res, "method", "?"))]
        m["alerts"] += 1
        m["exactly_one" if n == 1 else ("none" if n == 0 else "several")] += 1
    resolution = {
        method: {"alerts": c["alerts"], "exactly_one": c["exactly_one"], "none": c["none"],
                 "several": c["several"], "pct_exactly_one": _pct(c["exactly_one"], c["alerts"])}
        for method, c in sorted(by_method.items())
    }

    n_hits = len(facts)
    dob_any = 0
    dob_src: Counter[str] = Counter()
    hit_c = cust_c = alert_c = both = both_alert = 0
    for hit_id in sorted(facts):
        f = facts[hit_id]
        if f is None:
            continue
        cands = getattr(f, "dob_candidates", ()) or ()
        if cands:
            dob_any += 1
        for src in sorted({str(getattr(c, "source", "?")) for c in cands}):
            dob_src[src] += 1
        hit_has = bool(getattr(f, "countries", ()) or getattr(f, "countries_raw", ()))
        hit = ds.hits.get(hit_id)
        alert = ds.alerts.get(getattr(hit, "alert_id", "")) if hit is not None else None
        res = resolutions.get(getattr(hit, "alert_id", "")) if hit is not None else None
        cust_has = False
        for person in getattr(res, "persons", ()) or ():
            for rec in getattr(person, "records", ()) or ():
                if getattr(rec, "residence_country", None) or getattr(rec, "nationality", ()):
                    cust_has = True
            for chk in getattr(person, "kyc", ()) or ():
                if getattr(chk, "document_country", None):
                    cust_has = True
        alert_has = bool(getattr(alert, "country", None))
        hit_c += hit_has
        cust_c += cust_has
        alert_c += alert_has
        both += hit_has and cust_has
        both_alert += hit_has and (cust_has or alert_has)
    return {
        "resolution_by_method": resolution,
        "hits": n_hits,
        "hit_dob_candidate_any": {"n": dob_any, "pct": _pct(dob_any, n_hits)},
        "hit_dob_candidate_by_source": {s: {"n": c, "pct": _pct(c, n_hits)} for s, c in sorted(dob_src.items())},
        "countries": {
            "hit_side": {"n": hit_c, "pct": _pct(hit_c, n_hits)},
            "customer_records_or_kyc": {"n": cust_c, "pct": _pct(cust_c, n_hits)},
            "alert_country": {"n": alert_c, "pct": _pct(alert_c, n_hits)},
            "both_hit_and_customer": {"n": both, "pct": _pct(both, n_hits)},
            "both_hit_and_customer_or_alert": {"n": both_alert, "pct": _pct(both_alert, n_hits)},
        },
    }


def build_report(data: str | Path) -> dict[str, Any]:
    from screening.facts import gather_hit_facts
    from screening.resolve import resolve_customer

    ds = load_dataset(data)
    decisions = {d.hit_id: d for d in run(ds)}  # the one pipeline run
    labels = dict(getattr(ds, "practice_labels", {}) or {})
    preds = [decisions[h].submission() for h in sorted(decisions)]
    scored = score(preds, labels) if labels else {}
    resolutions = {a: _safe(resolve_customer, ds, ds.alerts[a]) for a in sorted(ds.alerts)}
    facts = {h: _safe(gather_hit_facts, ds, ds.hits[h]) for h in sorted(ds.hits)}
    diffs = label_diffs(decisions, labels)
    counts = Counter(k for d in diffs for k in d["kinds"])
    return {
        "data": str(data),
        "score": {k: v for k, v in scored.items() if k != "rows"},
        "failure_counts": {k: counts.get(k, 0) for k, _ in FAILURE_ORDER},
        "label_diffs": diffs,
        "risk": risk_scan(decisions, labels, resolutions),
        "stages": stage_diagnostics(ds, resolutions, facts),
    }


# ------------------------------------------------------------------ rendering

def headline(rep: dict[str, Any]) -> str:
    s, fc, risk = rep["score"], rep["failure_counts"], rep["risk"]
    return (f"decision_score {s.get('decision_score')} | unsafe_clearances {s.get('unsafe_clearances')} | "
            f"review_rate {s.get('review_rate')} | label failures: "
            + ", ".join(f"{k} {v}" for k, v in fc.items() if v)
            + f" | risk candidates on unlabelled hits: {len(risk['candidates'])}")


def _render_diff(d: dict[str, Any]) -> list[str]:
    out = [
        f"#### {d['hit_id']} (alert {d['alert_id']}) - {', '.join(d['kinds'])}",
        "",
        "| | truth | predicted |",
        "|---|---|---|",
        f"| decision | {d['truth_decision']} | {d['pred_decision']} |",
        f"| reason | {' / '.join(d['truth_reasons']) or '-'} | {d['pred_reason']} |",
        f"| customer | {' / '.join(d['truth_customers']) or '-'} | {d['pred_customer']} |",
        f"| evidence | {', '.join(d['truth_evidence']) or '-'} | {', '.join(d['pred_evidence']) or '-'} |",
        "",
        f"- evidence missing: {', '.join(d['evidence_missing']) or '-'}; "
        f"extra: {', '.join(d['evidence_extra']) or '-'}",
        f"- tier {d['tier']}, flags {', '.join(d['flags']) or '-'}",
        f"- label path: {_short(d['path'], 600) or '-'}",
        f"- reference reason: {_short(d['reference_reason'], 600) or '-'}",
        f"- our reason: {_short(d['pred_reason_text'], 600) or '-'}",
        "",
    ]
    if d["trace"]:
        out += ["| rule_id | signal | reason | weak | summary | evidence |", "|---|---|---|---|---|---|"]
        for t in d["trace"]:
            weak = t.get("weak_why") or ("yes" if t.get("weak") else "")
            out.append(f"| {t.get('rule_id')} | {t.get('signal')} | {t.get('reason') or ''} | {weak} | "
                       f"{_md(t.get('summary'))} | {', '.join(t.get('evidence') or [])} |")
        out.append("")
    else:
        out += ["_(no rule trace)_", ""]
    return out


def render_markdown(rep: dict[str, Any]) -> str:
    s = rep["score"]
    lines = ["# Error-analysis report", "", f"Data: `{rep['data']}`", "", f"**Headline:** {headline(rep)}", "",
             "## 1. Score summary", ""]
    for key in ("labelled_hits", "decision_score", "decision_accuracy", "unsafe_clearances", "review_rate",
                "reason_code_accuracy_on_correct", "customer_id_accuracy_on_correct",
                "evidence_precision", "evidence_recall"):
        lines.append(f"- {key}: {s.get(key)}")
    if s.get("confusion"):
        lines += ["", "Confusion (truth->pred): " + ", ".join(f"{k} {v}" for k, v in s["confusion"].items())]
    lines += ["", "## 2. Practice-label failures", "", "| failure type | labels |", "|---|---|"]
    for key, title in FAILURE_ORDER:
        lines.append(f"| {title} | {rep['failure_counts'].get(key, 0)} |")
    lines.append("")
    by_primary: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in rep["label_diffs"]:
        by_primary[d["primary"]].append(d)
    for key, title in FAILURE_ORDER:
        group = by_primary.get(key, [])
        if not group:
            continue
        lines += [f"### {title} ({len(group)})", ""]
        for d in group:
            lines += _render_diff(d)

    risk = rep["risk"]
    lines += ["## 3. Risk scan (all hits)", "", "Decisions by tier: "
              + ", ".join(f"{k} {v}" for k, v in risk["by_tier"].items()), "",
              "Decision/tier: " + ", ".join(f"{k} {v}" for k, v in risk["by_decision_tier"].items()), "",
              f"### Candidate unsafe clearances on unlabelled hits ({len(risk['candidates'])} of "
              f"{risk['unlabelled_hits']} unlabelled)", "",
              f"DIFFERENT_ENTITY via {' / '.join(RISKY_REASONS)} with at least one danger signal. Eyeball these.", ""]
    if risk["candidates"]:
        lines += ["| hit | reason | tier | customer | method | danger signals | deciding rule(s) |",
                  "|---|---|---|---|---|---|---|"]
        for c in risk["candidates"]:
            lines.append(f"| {c['hit_id']} | {c['reason_code']} | {c['tier']} | {c['customer_record_id']} | "
                         f"{c['resolution_method']} | {'; '.join(c['dangers'])} | "
                         f"{_md(' // '.join(c['deciding']))} |")
    else:
        lines.append("_none_")

    st = rep["stages"]
    lines += ["", "## 4. Stage diagnostics", "", "### Customer resolution by method", "",
              "| method | alerts | exactly one | none | several | % exactly one |", "|---|---|---|---|---|---|"]
    for m, r in st["resolution_by_method"].items():
        lines.append(f"| {m} | {r['alerts']} | {r['exactly_one']} | {r['none']} | {r['several']} | "
                     f"{r['pct_exactly_one']} |")
    lines += ["", f"### Hit DOB candidates ({st['hits']} hits)", "",
              f"- any source: {st['hit_dob_candidate_any']['n']} ({st['hit_dob_candidate_any']['pct']})"]
    for src, r in st["hit_dob_candidate_by_source"].items():
        lines.append(f"- {src}: {r['n']} ({r['pct']})")
    lines += ["", "### Countries", ""]
    for k, r in st["countries"].items():
        lines.append(f"- {k}: {r['n']} ({r['pct']})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data" / "dev"), help="data folder")
    ap.add_argument("--out", default="out/error_report.md", help="markdown report path")
    ap.add_argument("--json", action="store_true", help="also print the full report as JSON on stdout")
    args = ap.parse_args(argv)
    rep = build_report(args.data)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(rep), encoding="utf-8")
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    else:
        print(headline(rep))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
