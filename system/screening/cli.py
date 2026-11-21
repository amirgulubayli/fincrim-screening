"""Command line for people, agents and bots. Standard library only.

  screen run DATA OUT [--trace FILE]   decide every hit; OUT = submission JSONL
  screen explain HIT_ID --data DATA     full evidence trail for one hit
  screen eval --data DATA [--pred F]    score against practice_labels.jsonl
  screen repro --data DATA --runs 5     run N times in fresh processes, prove identical
  screen summary --data DATA            dataset + decision counts by HITL tier
Add --json to any command for machine-readable output (Grok Bot / CI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .evaluate import score
from .loaders import load_dataset
from .pipeline import adjudicate, run

SCREEN_PY = Path(__file__).resolve().parent.parent / "screen.py"


def _dump(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        for key, value in obj.items():
            print(f"{key:>34}: {value}")


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    ds = load_dataset(args.data)
    decisions = run(ds)
    _dump([d.submission() for d in decisions], Path(args.out))
    if args.trace:
        _dump([d.full() for d in decisions], Path(args.trace))
    summary = {
        "engine_version": __version__,
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d.decision.value for d in decisions).items())),
        "tiers": dict(sorted(Counter(d.tier.value for d in decisions).items())),
        "seconds": round(time.perf_counter() - started, 2),
        "output": str(args.out),
    }
    if not args.quiet:
        _print(summary, args.json)
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    if args.hit_id not in ds.hits:
        print(f"unknown hit {args.hit_id}", file=sys.stderr)
        return 2
    d = adjudicate(ds, args.hit_id)
    if args.json:
        print(json.dumps(d.full(), indent=2, ensure_ascii=False))
        return 0
    print(f"{d.hit_id} (alert {d.alert_id}) -> {d.decision.value} / {d.reason_code.value}")
    print(f"  tier: {d.tier.value}   flags: {', '.join(d.flags) or '-'}")
    print(f"  customer: {d.customer_record_id}   evidence: {', '.join(d.evidence) or '-'}")
    print(f"  reason: {d.reason}")
    print("  trace:")
    for r in d.trace:
        weak = f" [WEAK: {r.weak_why}]" if r.weak else ""
        print(f"    {r.rule_id:<12} {r.signal.value:<13} {r.summary}{weak}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    if not ds.practice_labels:
        print("no practice_labels.jsonl in the data folder", file=sys.stderr)
        return 2
    if args.pred:
        with open(args.pred, encoding="utf-8") as fh:
            preds = [json.loads(line) for line in fh if line.strip()]
    else:
        preds = [d.submission() for d in run(ds)]
    result = score(preds, ds.practice_labels)
    rows = result.pop("rows")
    _print(result, args.json)
    if args.verbose and not args.json:
        for r in rows:
            if not (r["correct"] and r["reason_ok"] and r["customer_ok"]):
                print(f"  {r['hit_id']}: truth={r['truth']} pred={r['pred']} "
                      f"reason_ok={r['reason_ok']} customer_ok={r['customer_ok']}"
                      f"{'  <-- UNSAFE CLEARANCE' if r['unsafe'] else ''}")
    return 1 if result["unsafe_clearances"] else 0


def cmd_repro(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = ["0", "1", "42", "1234", "987654"][: args.runs] + [
        str(i) for i in range(100, 100 + max(0, args.runs - 5))
    ]
    runs = []
    for i, seed in enumerate(seeds, 1):
        path = out_dir / f"run_{i}.jsonl"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        t0 = time.perf_counter()
        subprocess.run([sys.executable, str(SCREEN_PY), "run", args.data, str(path), "--quiet"],
                       check=True, env=env)
        data = path.read_bytes()
        decisions = {}
        for line in data.decode("utf-8").splitlines():
            row = json.loads(line)
            decisions[row["hit_id"]] = (row["decision"], row["reason_code"])
        runs.append({"run": i, "pythonhashseed": seed, "sha256": hashlib.sha256(data).hexdigest(),
                     "seconds": round(time.perf_counter() - t0, 2), "decisions": decisions})
    first = runs[0]["decisions"]
    unstable = sorted(h for h in first if any(r["decisions"].get(h) != first[h] for r in runs[1:]))
    report = {
        "runs": [{k: v for k, v in r.items() if k != "decisions"} for r in runs],
        "byte_identical": len({r["sha256"] for r in runs}) == 1,
        "hits": len(first),
        "unstable_hits": unstable,
        "decision_reproducibility": round(1 - len(unstable) / len(first), 4) if first else None,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _print(report, args.json)
    return 0 if report["byte_identical"] else 1


def cmd_summary(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    _print({"data": str(ds.root), **ds.summary()}, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="screen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("run", help="decide every hit")
    sp.add_argument("data")
    sp.add_argument("out")
    sp.add_argument("--trace", help="also write tiers, flags and rule trace (dashboard input)")
    sp.add_argument("--quiet", action="store_true")
    common(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("explain", help="evidence trail for one hit")
    sp.add_argument("hit_id")
    sp.add_argument("--data", default="data/dev")
    common(sp)
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("eval", help="score against practice labels")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--pred", help="score an existing output file instead of running")
    sp.add_argument("-v", "--verbose", action="store_true")
    common(sp)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("repro", help="prove N runs are identical")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--runs", type=int, default=5)
    sp.add_argument("--out-dir", default="output/repro")
    common(sp)
    sp.set_defaults(func=cmd_repro)

    sp = sub.add_parser("summary", help="dataset counts")
    sp.add_argument("--data", default="data/dev")
    common(sp)
    sp.set_defaults(func=cmd_summary)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
