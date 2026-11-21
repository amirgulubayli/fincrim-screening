"""Deterministic half of the Grok Bot morning run. Standard library only.

Runs the engine (summary + decision/tier counts + review queue), scores it against the
practice labels, proves N runs are byte-identical, compares with the previous morning
report, writes out/morning/YYYY-MM-DD.md + .json and prints the JSON summary.

  python tools/morning_run.py [--data data/dev] [--runs 5] [--out-dir out/morning]

Exit code 1 when a regression is found (unsafe clearance, repro not identical, score
drop, crash, runtime over 10 min), so a scheduler can alert. Never edits code; the only
files it writes are the two reports. Posting to GitHub is left to the morning-run skill
(Grok) or tools/morning_run.ps1.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SYSTEM = REPO / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))

MAX_SECONDS = 600


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_engine(data: Path, queue_limit: int) -> dict[str, Any]:
    """Summary, decision counts, review queue and practice-label score, in-process."""
    from screening import __version__
    from screening.evaluate import score
    from screening.loaders import load_dataset
    from screening.pipeline import run

    t0 = time.perf_counter()
    ds = load_dataset(data)
    decisions = run(ds)
    seconds = round(time.perf_counter() - t0, 2)

    def queue(tier: str) -> dict[str, Any]:
        rows = [d for d in decisions if d.tier.value == tier]
        return {"total": len(rows), "hits": [
            {"hit_id": d.hit_id, "decision": d.decision.value, "reason_code": d.reason_code.value,
             "flags": list(d.flags)} for d in rows[:queue_limit]]}

    out: dict[str, Any] = {
        "engine_version": __version__,
        "dataset": ds.summary(),
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d.decision.value for d in decisions).items())),
        "tiers": dict(sorted(Counter(d.tier.value for d in decisions).items())),
        "engine_errors": sorted(d.hit_id for d in decisions
                                if any(f.startswith("ENGINE_ERROR") for f in d.flags)),
        "seconds": seconds,
        "queue": {"HUMAN_REVIEW": queue("HUMAN_REVIEW"), "BORDERLINE": queue("BORDERLINE")},
        "eval": None,
    }
    if ds.practice_labels:
        result = score([d.submission() for d in decisions], ds.practice_labels)
        rows = result.pop("rows")
        result["unsafe_hits"] = [r["hit_id"] for r in rows if r["unsafe"]]
        out["eval"] = result
    return out


def run_repro(data: Path, runs: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="morning-repro-") as tmp:
        proc = subprocess.run(
            [sys.executable, str(SYSTEM / "screen.py"), "repro", "--data", str(data), "--runs", str(runs),
             "--out-dir", tmp, "--json"],
            capture_output=True, text=True, encoding="utf-8", cwd=REPO, timeout=3600,
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"repro exit {proc.returncode}: {proc.stderr.strip()[-500:]}"}
    report["exit_code"] = proc.returncode
    return report


def previous_report(out_dir: Path, today: str, data_label: str) -> dict[str, Any] | None:
    """Most recent earlier report for the same data folder (so dev is never compared with a fixture)."""
    for path in sorted((p for p in out_dir.glob("????-??-??.json") if p.stem < today), reverse=True):
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if prev.get("data_label") == data_label:
            return prev
    return None


def find_regressions(report: dict[str, Any], prev: dict[str, Any] | None) -> list[str]:
    regs: list[str] = []
    for name, err in report["errors"].items():
        regs.append(f"{name} crashed: {err.splitlines()[-1] if err else 'error'}")
    engine = report.get("engine") or {}
    ev = engine.get("eval") or {}
    if ev.get("unsafe_clearances"):
        regs.append(f"{ev['unsafe_clearances']} unsafe clearance(s): {', '.join(ev.get('unsafe_hits', []))}")
    if engine.get("engine_errors"):
        regs.append(f"engine fell back to REVIEW on errors for {len(engine['engine_errors'])} hit(s)")
    repro = report.get("repro")
    if repro and "error" in repro:
        regs.append(f"repro failed: {repro['error']}")
    elif repro and repro.get("byte_identical") is False:
        regs.append(f"repro not byte-identical ({len(repro.get('unstable_hits', []))} unstable hit(s))")
    if engine.get("seconds", 0) > MAX_SECONDS:
        regs.append(f"runtime {engine['seconds']}s exceeds {MAX_SECONDS}s")
    prev_score = ((prev or {}).get("engine") or {}).get("eval", {}) or {}
    if ev.get("decision_score") is not None and prev_score.get("decision_score") is not None \
            and ev["decision_score"] < prev_score["decision_score"]:
        regs.append(f"decision score dropped {prev_score['decision_score']} -> {ev['decision_score']}")
    return regs


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "NO"
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items()) or "-"
    return str(value)


def render_markdown(report: dict[str, Any], prev: dict[str, Any] | None) -> str:
    e = report.get("engine") or {}
    ev = e.get("eval") or {}
    r = report.get("repro") or {}
    pe = (prev or {}).get("engine") or {}
    pev = pe.get("eval") or {}
    pr = (prev or {}).get("repro") or {}
    runs = len(r.get("runs", [])) or report["runs"]
    rows = [
        ("hits", e.get("hits"), pe.get("hits")),
        ("decisions", e.get("decisions"), pe.get("decisions")),
        ("tiers", e.get("tiers"), pe.get("tiers")),
        ("decision score", ev.get("decision_score"), pev.get("decision_score")),
        ("decision accuracy", ev.get("decision_accuracy"), pev.get("decision_accuracy")),
        ("unsafe clearances", ev.get("unsafe_clearances"), pev.get("unsafe_clearances")),
        ("review rate", ev.get("review_rate"), pev.get("review_rate")),
        (f"byte-identical ({runs} runs)", r.get("byte_identical"), pr.get("byte_identical")),
        ("runtime (s)", e.get("seconds"), pe.get("seconds")),
    ]
    prev_label = f"Previous ({prev['date']})" if prev else "Previous"
    lines = [
        f"## Morning run {report['date']} (main @ {report['git_sha']})",
        "",
        "### Summary",
        "",
        "_Deterministic report from `tools/morning_run.py`; the engine's numbers are final._",
        "",
        f"| Metric | Today | {prev_label} |",
        "|---|---|---|",
        *[f"| {name} | {_fmt(today)} | {_fmt(before)} |" for name, today, before in rows],
        "",
        "### Regressions",
        "",
        *([f"- {x}" for x in report["regressions"]] or ["none"]),
        "",
    ]
    for tier, what in (("HUMAN_REVIEW", "analyst queue"), ("BORDERLINE", "4-eyes QA, weak facts")):
        q = (e.get("queue") or {}).get(tier) or {"total": 0, "hits": []}
        lines += [f"### {tier} ({what}): {q['total']}", ""]
        if q["hits"]:
            lines += ["| hit | decision | reason code | flags |", "|---|---|---|---|"]
            lines += [f"| {h['hit_id']} | {h['decision']} | {h['reason_code']} | {', '.join(h['flags']) or '-'} |"
                      for h in q["hits"]]
            if q["total"] > len(q["hits"]):
                lines.append(f"| ... {q['total'] - len(q['hits'])} more | | | |")
        else:
            lines.append("none")
        lines.append("")
    if r.get("runs"):
        lines += ["### Repro", "", "| run | PYTHONHASHSEED | sha256 | seconds |", "|---|---|---|---|"]
        lines += [f"| {x['run']} | {x['pythonhashseed']} | `{x['sha256'][:16]}` | {x['seconds']} |"
                  for x in r["runs"]]
        lines.append("")
    lines += [f"_data: `{report['data_label']}` · engine {e.get('engine_version', '-')} · "
              f"generated by tools/morning_run.py_", ""]
    return "\n".join(lines)


def build_report(data: Path, runs: int, today: str, out_dir: Path, queue_limit: int,
                 repro: bool = True) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Returns (today's report, the previous report it was compared with)."""
    report: dict[str, Any] = {"date": today, "git_sha": git_sha(), "data_label": data.name,
                              "runs": runs, "engine": None, "repro": None, "errors": {}}
    try:
        report["engine"] = run_engine(data, queue_limit)
    except Exception:  # noqa: BLE001 - a crash is itself a finding
        report["errors"]["engine"] = traceback.format_exc()
    if repro:
        try:
            report["repro"] = run_repro(data, runs)
        except Exception:  # noqa: BLE001
            report["errors"]["repro"] = traceback.format_exc()
    prev = previous_report(out_dir, today, data.name)
    report["previous_date"] = prev["date"] if prev else None
    report["regressions"] = find_regressions(report, prev)
    report["healthy"] = not report["regressions"]
    return report, prev


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data/dev")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--out-dir", default="out/morning")
    p.add_argument("--date", help="report date YYYY-MM-DD (default: today, local time)")
    p.add_argument("--queue-limit", type=int, default=15)
    p.add_argument("--no-repro", action="store_true", help="skip the N-run determinism check")
    a = p.parse_args(argv)

    data = Path(a.data)
    if not data.is_absolute() and not data.exists():
        data = REPO / data
    out_dir = Path(a.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO / out_dir
    today = a.date or dt.date.today().isoformat()
    if not data.is_dir():
        print(json.dumps({"error": f"data folder not found: {a.data}"}), file=sys.stderr)
        return 2

    report, prev = build_report(data, a.runs, today, out_dir, a.queue_limit, repro=not a.no_repro)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path = out_dir / f"{today}.md", out_dir / f"{today}.json"
    md_path.write_text(render_markdown(report, prev), encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

    ev = (report.get("engine") or {}).get("eval") or {}
    print(json.dumps({
        "date": today,
        "healthy": report["healthy"],
        "decision_score": ev.get("decision_score"),
        "unsafe_clearances": ev.get("unsafe_clearances"),
        "byte_identical": (report.get("repro") or {}).get("byte_identical"),
        "tiers": (report.get("engine") or {}).get("tiers"),
        "regressions": report["regressions"],
        "report_md": str(md_path),
        "report_json": str(json_path),
        "issue_title": f"Morning run {today}",
    }, indent=2))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
