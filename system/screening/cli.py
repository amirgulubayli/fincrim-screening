"""Command line for people, agents and bots (dashboard terminal, Grok plugin). Standard library only.

  screen run DATA OUT [--trace FILE]         decide every hit; OUT = submission JSONL; prints tiers +
                                             engine agreement counts
  screen explain HIT_ID --data DATA          final decision, core vs independent check, facet trace,
                                             agreement, customer resolution path, evidence, policies
  screen eval --data DATA [--pred F]         score against practice_labels.jsonl
  screen eval --data DATA --compare          score core, check and combined side by side
  screen repro --data DATA --runs 5          run N times in fresh processes, prove identical
  screen summary --data DATA                 dataset + decision counts by HITL tier
  screen diff --data DATA [--pair C:K]       where the decision core and the independent check disagree
  screen queue --tier TIER --data DATA       the analyst queue for one tier, one-line why per hit
  screen policy [ID ...]                     the policy registry (SOP P1-P9, HOFC-1..9, DC-1..3)
  screen stress --data DATA [--kinds K ...]  contrib/aml-bot perturbation stress test against OUR engine

--engine core|check|combined (run/eval/explain/repro/diff/queue/stress) selects the engine; default is
$SCREEN_ENGINE or combined. Add --json to any command for machine-readable output.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .compiler import ENGINES_CONFLICT, ENGINES_DISAGREE, merge_with_core
from .contracts import REASONS_BY_DECISION, Decision, HitDecision, ReasonCode, Signal, Tier
from .evaluate import score
from .loaders import load_dataset
from .pipeline import ENGINES, check_hit, engine_mode, fallback, run
from .policy import BY_ID, REGISTRY, policies_for
from .resolve import resolve_customer

SCREEN_PY = Path(__file__).resolve().parent.parent / "screen.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
STRESS_PY = REPO_ROOT / "contrib" / "aml-bot" / "engine" / "stress_test.py"
TIERS = tuple(t.value for t in Tier)
DECISION_ABBR = {"D": "DIFFERENT_ENTITY", "DIFF": "DIFFERENT_ENTITY", "DIFFERENT": "DIFFERENT_ENTITY",
                 "S": "SAME_ENTITY", "SAME": "SAME_ENTITY", "R": "REVIEW", "REV": "REVIEW"}


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


def _mode(args: argparse.Namespace) -> str:
    return getattr(args, "engine", None) or engine_mode()


# --------------------------------------------------------------------------- shared helpers

_REVIEWER_RE = re.compile(r"Reviewer:\s*(.+?\.)(?=\s|$)", re.S)


def reviewer_action(reason: str | None) -> str | None:
    """The 'Reviewer: ...' guidance sentence of an 'Escalated, not cleared' reason, if any."""
    m = _REVIEWER_RE.search(reason or "")
    return m.group(1).strip() if m else None


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    m = re.search(r"(?<=[a-z0-9)\]'])\.\s", text)
    s = text[: m.start() + 1] if m else text
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def core_decision(core: dict[str, Any] | None) -> Decision | None:
    """The core's decision as merge_with_core reads it (invalid values / codes -> REVIEW)."""
    if core is None:
        return None
    try:
        decision = Decision(core.get("decision"))
    except ValueError:
        return Decision.REVIEW
    try:
        code = ReasonCode(core.get("reason_code"))
    except ValueError:
        return Decision.REVIEW
    return decision if code in REASONS_BY_DECISION[decision] else Decision.REVIEW


def agreement(core: dict[str, Any] | None, check: HitDecision | None) -> str:
    """AGREE / DISAGREE / CONFLICT (core clears, check confirms) / N/A (an engine missing)."""
    c = core_decision(core)
    if c is None or check is None:
        return "N/A"
    if c is check.decision:
        return "AGREE"
    if c is Decision.DIFFERENT_ENTITY and check.decision is Decision.SAME_ENTITY:
        return "CONFLICT"
    return "DISAGREE"


def _core_for_hit(ds, hit_id: str) -> dict[str, Any] | None:
    """The core's decision for ONE hit without deciding all the others (fast `explain`).

    Mirrors core.run_core exactly for that hit: the first alert (sorted by id) that lists it decides it,
    with the same error guards and second_check. Falls back to the full run if the core cannot load.
    """
    from .core import core_results

    cached = getattr(ds, "_core_results", None)
    if cached is not None:
        return cached.get(hit_id)
    from .core import aml_engine as E

    E.LOAD_WARNINGS.clear()
    try:
        D = E.Data(str(ds.root))
    except Exception:  # noqa: BLE001 - same answer as the full run, just slower
        return core_results(ds).get(hit_id)
    hits = D.hits
    hit = hits.get(hit_id)
    if hit is None:
        return None
    for alert in sorted([a for a in D.alerts if a.get("alert_id")], key=lambda a: str(a["alert_id"])):
        listed = E.as_list(alert.get("hit_ids"))
        if hit_id not in listed and hit.get("alert_id") != alert["alert_id"]:
            continue
        try:
            persons, how = E.resolve_customer(D, alert)
        except Exception as exc:  # noqa: BLE001
            return E.error_review(hit_id, alert["alert_id"], f"customer lookup failed: {type(exc).__name__}")
        try:
            r = E.decide(D, alert, hit, persons, how)
            if r["decision"] == "DIFFERENT_ENTITY":
                r = E.second_check(D, alert, hit, persons, r)
            return r
        except Exception as exc:  # noqa: BLE001
            return E.error_review(hit_id, alert["alert_id"], f"{type(exc).__name__} while deciding")
    return E.error_review(hit_id, hit.get("alert_id"), "its alert was not found")


def _safe_check(ds, hit_id: str) -> tuple[HitDecision, bool]:
    """(check decision, errored). An erroring check becomes the pipeline's CHECK_ERROR fallback."""
    try:
        return check_hit(ds, hit_id), False
    except Exception as exc:  # noqa: BLE001
        return fallback(hit_id, ds.hits[hit_id].alert_id, f"CHECK_ERROR:{type(exc).__name__}"), True


def _final(ds, hit_id: str, mode: str, core: dict[str, Any] | None, check: HitDecision,
           check_err: bool) -> HitDecision:
    """Same result as pipeline.run(ds, mode) for this hit, reusing already computed engine outputs."""
    alert_id = ds.hits[hit_id].alert_id
    try:
        if mode == "check":
            if check_err:
                return fallback(hit_id, alert_id, check.flags[0].replace("CHECK_ERROR", "ENGINE_ERROR"))
            return check
        return merge_with_core(core, None if mode == "core" else check, hit_id, alert_id)
    except Exception as exc:  # noqa: BLE001
        return fallback(hit_id, alert_id, f"ENGINE_ERROR:{type(exc).__name__}")


def _vs_core(signal: Signal, core: Decision | None) -> str:
    if signal is Signal.NEUTRAL or core in (None, Decision.REVIEW):
        return "neutral"
    supports = (signal is Signal.CONTRADICTION) == (core is Decision.DIFFERENT_ENTITY)
    return "supports" if supports else "contradicts"


# --------------------------------------------------------------------------- commands


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    mode = _mode(args)
    ds = load_dataset(args.data)
    decisions = run(ds, mode)
    _dump([d.submission() for d in decisions], Path(args.out))
    if args.trace:
        _dump([d.full() for d in decisions], Path(args.trace))
    summary = {
        "engine_version": __version__,
        "engine": mode,
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d.decision.value for d in decisions).items())),
        "tiers": dict(sorted(Counter(d.tier.value for d in decisions).items())),
        "engine_agreement": _agreement_counts(decisions, mode),
        "seconds": round(time.perf_counter() - started, 2),
        "output": str(args.out),
    }
    if not args.quiet:
        _print(summary, args.json)
    return 0


def _agreement_counts(decisions: list[HitDecision], mode: str) -> dict[str, int] | None:
    """From the merge flags; only the combined engine compares the two engines."""
    if mode != "combined":
        return None
    conflict = sum(ENGINES_CONFLICT in d.flags for d in decisions)
    disagree = sum(ENGINES_DISAGREE in d.flags for d in decisions)
    return {"agree": len(decisions) - conflict - disagree, "disagree": disagree, "conflict": conflict}


def explain_hit(ds, hit_id: str, mode: str) -> dict[str, Any]:
    """Everything an analyst (or the dashboard / Grok plugin) needs about one hit."""
    core = _core_for_hit(ds, hit_id)
    check, check_err = _safe_check(ds, hit_id)
    final = _final(ds, hit_id, mode, core, check, check_err)
    cdec = core_decision(core)
    out = final.full()  # backward compatible top-level keys
    out["engine"] = mode
    out["reviewer_action"] = reviewer_action(final.reason)
    out["core"] = None if core is None else {
        "decision": core.get("decision"),
        "reason_code": core.get("reason_code"),
        "customer_record_id": core.get("customer_record_id"),
        "reason": core.get("reason"),
        "reviewer_action": reviewer_action(core.get("reason")),
        "escalated_by_policy": str(core.get("reason") or "").startswith("Escalated, not cleared"),
        "evidence": [str(e) for e in core.get("evidence") or () if e],
    }
    out["check"] = {
        "decision": check.decision.value,
        "reason_code": check.reason_code.value,
        "customer_record_id": check.customer_record_id,
        "reason": check.reason,
        "tier": check.tier.value,
        "flags": list(check.flags),
        "errored": check_err,
        "facets": [{**r.to_dict(), "vs_core": _vs_core(r.signal, cdec)} for r in check.trace],
    }
    out["agreement"] = agreement(core, None if check_err else check)
    alert = ds.alerts.get(ds.hits[hit_id].alert_id)
    if alert is None:
        out["resolution"] = None
    else:
        res = resolve_customer(ds, alert)
        out["resolution"] = {
            "method": res.method,
            "path": list(res.path),
            "candidates": list(res.candidates),
            "persons": [{"key": p.key, "record_ids": list(p.record_ids)} for p in res.persons],
        }
    out["policies"] = policies_for(final)
    out["policy_titles"] = {pid: BY_ID[pid].title for pid in out["policies"]}
    return out


def cmd_explain(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    if args.hit_id not in ds.hits:
        print(f"unknown hit {args.hit_id}", file=sys.stderr)
        return 2
    x = explain_hit(ds, args.hit_id, _mode(args))
    if args.json:
        print(json.dumps(x, indent=2, ensure_ascii=False))
        return 0
    print(f"{x['hit_id']} (alert {x['alert_id']}) -> {x['decision']} / {x['reason_code']}   [engine {x['engine']}]")
    print(f"  tier: {x['tier']}   flags: {', '.join(x['flags']) or '-'}   agreement: {x['agreement']}")
    print(f"  customer: {x['customer_record_id']}   evidence: {', '.join(x['evidence']) or '-'}")
    print(f"  reason: {x['reason']}")
    if x["reviewer_action"]:
        print(f"  REVIEWER ACTION: {x['reviewer_action']}")
    core = x["core"]
    if core:
        print(f"  core:  {core['decision']} / {core['reason_code']}"
              f"{'   (escalated by policy)' if core['escalated_by_policy'] else ''}")
        print(f"         {core['reason']}")
        if core["reviewer_action"] and core["reviewer_action"] != x["reviewer_action"]:
            print(f"         reviewer: {core['reviewer_action']}")
    else:
        print("  core:  (no answer)")
    chk = x["check"]
    print(f"  check: {chk['decision']} / {chk['reason_code']}   flags: {', '.join(chk['flags']) or '-'}")
    for f in chk["facets"]:
        weak = f" [WEAK: {f['weak_why']}]" if f["weak"] else ""
        print(f"    {f['rule_id']:<12} {f['signal']:<13} {f['vs_core']:<11} {f['summary']}{weak}")
    res = x["resolution"]
    if res:
        persons = ", ".join("+".join(p["record_ids"]) for p in res["persons"]) or "none"
        print(f"  resolution: {res['method']} -> {persons}")
        for step in res["path"]:
            print(f"    - {step}")
    print("  policies: " + (", ".join(f"{p} ({t})" for p, t in x["policy_titles"].items()) or "-"))
    return 0


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "rows"}


def cmd_eval(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    if not ds.practice_labels:
        print("no practice_labels.jsonl in the data folder", file=sys.stderr)
        return 2
    mode = _mode(args)
    if args.compare:
        engines = {m: _metrics(score([d.submission() for d in run(ds, m)], ds.practice_labels))
                   for m in ("core", "check", "combined")}
        if args.json:
            print(json.dumps({"selected": mode, "engines": engines}, indent=2, ensure_ascii=False))
        else:
            print(f"{'metric':>34}  {'core':>12} {'check':>12} {'combined':>12}")
            for key in engines["combined"]:
                if key == "confusion":
                    continue
                print(f"{key:>34}  " + " ".join(f"{str(engines[m][key]):>12}" for m in engines))
            for m, r in engines.items():
                print(f"{'confusion ' + m:>34}  {r['confusion']}")
        return 1 if engines[mode]["unsafe_clearances"] else 0
    if args.pred:
        with open(args.pred, encoding="utf-8") as fh:
            preds = [json.loads(line) for line in fh if line.strip()]
    else:
        preds = [d.submission() for d in run(ds, mode)]
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
    engine_args = ["--engine", args.engine] if args.engine else []
    runs = []
    for i, seed in enumerate(seeds, 1):
        path = out_dir / f"run_{i}.jsonl"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        t0 = time.perf_counter()
        subprocess.run([sys.executable, str(SCREEN_PY), "run", args.data, str(path), "--quiet", *engine_args],
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
        "engine": _mode(args),
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


def _parse_pair(text: str) -> tuple[str, str]:
    parts = re.split(r"[:,/>|]+", text.strip().upper())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--pair wants CORE:CHECK, e.g. DIFFERENT_ENTITY:REVIEW or D:R")
    return tuple(DECISION_ABBR.get(p, p) for p in parts)  # type: ignore[return-value]


def engine_diff(ds) -> dict[str, Any]:
    """Core vs independent check on every hit (raw engine outputs, before the merge)."""
    from .core import core_results

    cores = core_results(ds)
    pairs: Counter[str] = Counter()
    status: Counter[str] = Counter()
    rows = []
    for hit_id in sorted(ds.hits):
        core = cores.get(hit_id)
        check, err = _safe_check(ds, hit_id)
        st = agreement(core, None if err else check)
        cdec = core_decision(core)
        c = cdec.value if cdec else "NONE"
        pairs[f"{c}:{check.decision.value}"] += 1
        status[st] += 1
        if st != "AGREE":
            rows.append({"hit_id": hit_id, "alert_id": ds.hits[hit_id].alert_id, "status": st,
                         "core": c, "check": check.decision.value,
                         "core_reason_code": (core or {}).get("reason_code"),
                         "check_reason_code": check.reason_code.value,
                         "core_customer": (core or {}).get("customer_record_id"),
                         "check_customer": check.customer_record_id})
    return {"hits": len(ds.hits), "status": dict(sorted(status.items())),
            "pairs": dict(sorted(pairs.items())), "rows": rows}


def cmd_diff(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    report = engine_diff(ds)
    if args.pair:
        want = f"{args.pair[0]}:{args.pair[1]}"
        report["pair_filter"] = want
        report["rows"] = [r for r in report["rows"] if f"{r['core']}:{r['check']}" == want]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"{report['hits']} hits   " + "   ".join(f"{k}: {v}" for k, v in report["status"].items()))
    print("pairs (core:check):")
    for pair, n in report["pairs"].items():
        c, k = pair.split(":")
        print(f"  {n:>5}  {pair}{'' if c == k else '   <- disagree'}")
    print(f"disagreements{' for ' + report['pair_filter'] if args.pair else ''}: {len(report['rows'])}")
    for r in report["rows"]:
        print(f"  {r['hit_id']:<10} {r['status']:<8} core={r['core']}/{r['core_reason_code']}  "
              f"check={r['check']}/{r['check_reason_code']}")
    return 0


def queue_items(decisions: list[HitDecision], tier: str) -> list[dict[str, Any]]:
    items = []
    for d in decisions:
        if d.tier.value != tier:
            continue
        action = reviewer_action(d.reason)
        why = action or _first_sentence(d.reason)
        if ENGINES_DISAGREE in d.flags and not action:
            why = f"engines disagree; {why}"
        items.append({"hit_id": d.hit_id, "alert_id": d.alert_id, "decision": d.decision.value,
                      "reason_code": d.reason_code.value, "customer_record_id": d.customer_record_id,
                      "flags": list(d.flags), "why": why, "reviewer_action": action})
    return items


def cmd_queue(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    mode = _mode(args)
    items = queue_items(run(ds, mode), args.tier)
    shown = items[: args.limit] if args.limit else items
    report = {"engine": mode, "tier": args.tier, "total": len(items), "shown": len(shown), "items": shown}
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"{args.tier}: {len(items)} hits{f' (showing {len(shown)})' if len(shown) < len(items) else ''}"
          f"   [engine {mode}]")
    for it in shown:
        flags = f" [{', '.join(it['flags'])}]" if it["flags"] else ""
        print(f"  {it['hit_id']:<10} {it['decision']:<16} {it['reason_code']:<24}{flags}  {it['why']}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    wanted = [i.upper() for i in args.ids]
    unknown = [i for i in wanted if i not in BY_ID]
    if unknown:
        print(f"unknown policy id(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    rows = [p.to_dict() for p in REGISTRY if not wanted or p.id in wanted]
    if args.json:
        print(json.dumps({"policies": rows}, indent=2, ensure_ascii=False))
        return 0
    for p in rows:
        print(f"{p['id']:<7} [{p['kind']}] {p['title']}")
        print(f"        effect: {p['effect']}")
        print(f"        implemented by: {'; '.join(p['implemented_by'])}")
        if p["triggers"]:
            print(f"        triggers: {'; '.join(p['triggers'])}")
    return 0


# --------------------------------------------------------------------------- stress


def _load_stress_module():
    """contrib/aml-bot/engine/stress_test.py (stdlib only). Only its perturbations are reused."""
    if not STRESS_PY.is_file():
        return None
    spec = importlib.util.spec_from_file_location("aml_bot_stress_test", STRESS_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # it imports the contrib engine (`screen`) for country codes
    return mod


def _decide_folder(folder: str | Path, mode: str) -> tuple[dict[str, str], str | None]:
    try:
        ds = load_dataset(folder)
        return {d.hit_id: d.decision.value for d in run(ds, mode)}, None
    except Exception as exc:  # noqa: BLE001 - a perturbation that breaks loading is reported, not fatal
        return {}, f"{type(exc).__name__}: {exc}"


def stress_report(data: str | Path, kinds: list[str] | None, mode: str) -> dict[str, Any]:
    st = _load_stress_module()
    if st is None:
        raise FileNotFoundError(f"stress test not found at {STRESS_PY}")
    src = str(data)
    kinds = kinds or list(st.KINDS)
    bad = [k for k in kinds if k not in st.KINDS]
    if bad:
        raise ValueError(f"unknown perturbation(s): {', '.join(bad)} (known: {', '.join(st.KINDS)})")
    base_tables = {t: st.read(src, t) for t in st.TABLES}
    variants: dict[str, set[str]] = {}
    nv_path = Path(src) / "name_variants.json"
    if nv_path.is_file():
        nv = json.loads(nv_path.read_text(encoding="utf-8"))
        for group in nv.get("given_name_groups", []):
            for n in group:
                variants[n.lower()] = set(group)
    base, err = _decide_folder(src, mode)
    if err:
        raise RuntimeError(f"baseline run failed: {err}")
    report: dict[str, Any] = {
        "engine": mode,
        "source": "contrib/aml-bot/engine/stress_test.py (perturbations) + screening.pipeline.run",
        "baseline": {"hits": len(base), "cleared": sum(v == "DIFFERENT_ENTITY" for v in base.values())},
        "kinds": {},
    }
    total = 0
    for kind in kinds:
        rng = st.random.Random(7)
        tmp = tempfile.mkdtemp(prefix=f"stress_{kind}_")
        try:
            tables = json.loads(json.dumps(base_tables))
            junk = st.perturb(tables, kind, rng, variants)
            for t, rows in tables.items():
                st.write(tmp, t, rows, junk=junk)
            for extra in sorted(os.listdir(src)):
                if extra not in st.TABLES and os.path.isfile(os.path.join(src, extra)):
                    shutil.copy(os.path.join(src, extra), tmp)
            new, error = _decide_folder(tmp, mode)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        flips: Counter[str] = Counter()
        unsafe = []
        for h in sorted(base):
            nd = new.get(h, "MISSING")
            if nd != base[h]:
                flips[f"{base[h]}->{nd}"] += 1
                if nd == "DIFFERENT_ENTITY":
                    unsafe.append(h)
        total += len(unsafe)
        report["kinds"][kind] = {"flips": dict(sorted(flips.items())), "unsafe": unsafe, "error": error}
    report["total_unsafe"] = total
    return report


def cmd_stress(args: argparse.Namespace) -> int:
    try:
        report = stress_report(args.data, args.kinds, _mode(args))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        b = report["baseline"]
        print(f"baseline: {b['hits']} hits, {b['cleared']} cleared   [engine {report['engine']}]")
        for kind, r in report["kinds"].items():
            extra = f"  e.g. {r['unsafe'][:5]}" if r["unsafe"] else ""
            err = f"  ERROR {r['error']}" if r["error"] else ""
            print(f"{kind:20s} unsafe={len(r['unsafe']):3d}  flips={r['flips']}{extra}{err}")
        print(f"TOTAL UNSAFE FLIPS: {report['total_unsafe']}")
    return 1 if report["total_unsafe"] else 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="screen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, engine: bool = False) -> None:
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if engine:
            sp.add_argument("--engine", choices=ENGINES, default=None,
                            help="core | check | combined (default: $SCREEN_ENGINE or combined)")

    sp = sub.add_parser("run", help="decide every hit")
    sp.add_argument("data")
    sp.add_argument("out")
    sp.add_argument("--trace", help="also write tiers, flags and rule trace (dashboard input)")
    sp.add_argument("--quiet", action="store_true")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("explain", help="evidence trail, core vs check, policies for one hit")
    sp.add_argument("hit_id")
    sp.add_argument("--data", default="data/dev")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("eval", help="score against practice labels")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--pred", help="score an existing output file instead of running")
    sp.add_argument("--compare", action="store_true", help="score core, check and combined side by side")
    sp.add_argument("-v", "--verbose", action="store_true")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("repro", help="prove N runs are identical")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--runs", type=int, default=5)
    sp.add_argument("--out-dir", default="output/repro")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_repro)

    sp = sub.add_parser("summary", help="dataset counts")
    sp.add_argument("--data", default="data/dev")
    common(sp)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("diff", help="decision core vs independent check disagreements")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--pair", type=_parse_pair, help="CORE:CHECK filter, e.g. DIFFERENT_ENTITY:REVIEW or D:R")
    common(sp)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("queue", help="analyst queue for one tier")
    sp.add_argument("--tier", required=True, type=str.upper, choices=TIERS)
    sp.add_argument("--limit", type=int, default=0, help="show at most N hits (0 = all)")
    sp.add_argument("--data", default="data/dev")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_queue)

    sp = sub.add_parser("policy", help="policy registry (SOP, Head of FinCrime calls, dual control)")
    sp.add_argument("ids", nargs="*", help="only these ids, e.g. HOFC-3 DC-1")
    common(sp)
    sp.set_defaults(func=cmd_policy)

    sp = sub.add_parser("stress", help="perturbation stress test (contrib/aml-bot) against our engine")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--kinds", nargs="*", help="perturbations to run (default: all 21)")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_stress)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
