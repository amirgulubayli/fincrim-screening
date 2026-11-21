"""Demo dashboard API (not scored). Wraps the stdlib engine under system/.

Run from the repo root:  uv run uvicorn app.server.main:app --port 8000
Data folder comes from env SCREEN_DATA (default data/dev, relative paths resolve from the repo root).
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))

from screening import __version__  # noqa: E402
from screening.evaluate import score  # noqa: E402
from screening.loaders import Dataset, load_dataset  # noqa: E402
from screening.resolve import resolve_customer  # noqa: E402

from .engines import ENGINE_FLAGS, engine_summary, engine_view, run_dual  # noqa: E402


def _data_dir() -> Path:
    p = Path(os.environ.get("SCREEN_DATA", "data/dev")).expanduser()
    return (p if p.is_absolute() else ROOT / p).resolve()


DATA_DIR = _data_dir()
REVIEWS = ROOT / "out" / "reviews.jsonl"
REPRO_DIR = ROOT / "output" / "repro"
DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
# diff / queue / policy and `--engine` / `eval --compare` are the dual-control CLI commands; they are
# whitelisted ahead of landing in system/screening/cli.py (an unknown one just prints argparse's error).
ALLOWED_SUBCOMMANDS = {"run", "explain", "eval", "repro", "summary", "diff", "queue", "policy"}
DATA_SUBCOMMANDS = {"explain", "eval", "repro", "summary", "diff", "queue"}  # take --data DIR
# Backslashes are turned into forward slashes first (Windows paths); argv is executed without a shell.
SHELL_META = re.compile(r"[;&|`$<>(){}\[\]*?!~\n\r\"']")
WRITABLE_DIRS = (ROOT / "output", ROOT / "out")
COMMAND_TIMEOUT = float(os.environ.get("SCREEN_TERMINAL_TIMEOUT", "240"))
REPRO_RUNS = 5
AUTOMATED_TIERS = {"AUTO_CLEAR", "AUTO_ESCALATE"}
REVIEW_DECISIONS = {"CONFIRM", "SAME_ENTITY", "DIFFERENT_ENTITY", "REVIEW"}
MIN_NOTE = 5


class State:
    ds: Dataset | None = None
    decisions: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {}
    lock = threading.Lock()
    compute_lock = threading.Lock()
    # startup readiness (two engines take ~70 s on dev): loading -> ready | error
    status = "loading"
    stage = "starting"
    progress: tuple[int, int] = (0, 0)
    started_at = time.time()
    error: str | None = None
    reviews_lock = threading.Lock()
    repro_running = False
    repro_error: str | None = None


STATE = State()


def _record(rec: Any) -> dict[str, Any] | None:
    if rec is None:
        return None
    d = dataclasses.asdict(rec)
    extra = d.pop("extra", None) or {}
    return {**d, **({"extra": extra} if extra else {})}


def _progress(stage: str, done: int, total: int) -> None:
    STATE.stage, STATE.progress = stage, (done, total)


def compute() -> dict[str, Any]:
    with STATE.compute_lock:
        return _compute()


def _compute() -> dict[str, Any]:
    t0 = time.perf_counter()
    _progress("loading data", 0, 0)
    ds = load_dataset(DATA_DIR)
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    results, core, checks = run_dual(ds, _progress)
    run_s = time.perf_counter() - t1
    _progress("summarising", len(results), len(results))
    decisions = {}
    for d in results:
        full = d.full()
        full["engines"] = engine_view(full, core.get(d.hit_id), checks.get(d.hit_id))
        decisions[d.hit_id] = full
    rule_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for d in decisions.values():
        for t in d["trace"]:
            rule_stats[t["rule_id"]][t["signal"]] += 1
    tiers = Counter(d["tier"] for d in decisions.values())
    automated = sum(tiers.get(t, 0) for t in AUTOMATED_TIERS)
    summary: dict[str, Any] = {
        "engine_version": __version__,
        "data": DATA_DIR.as_posix(),
        "dataset": ds.summary(),
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d["decision"] for d in decisions.values()).items())),
        "tiers": dict(sorted(tiers.items())),
        "reason_codes": dict(sorted(Counter(d["reason_code"] for d in decisions.values()).items())),
        "flags": dict(sorted(Counter(f for d in decisions.values() for f in d["flags"]).items())),
        "rule_stats": {k: dict(sorted(v.items())) for k, v in sorted(rule_stats.items())},
        "automation": {
            "automated": automated,
            "needs_human": len(decisions) - automated,
            "rate": round(automated / len(decisions), 4) if decisions else None,
        },
        "engines": engine_summary([d["engines"] for d in decisions.values()]),
        "load_seconds": round(load_s, 2),
        "run_seconds": round(run_s, 2),
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eval": None,
    }
    if ds.practice_labels:
        ev = score([{k: v for k, v in d.items() if k not in ("tier", "flags", "trace", "engines")}
                    for d in decisions.values()], ds.practice_labels)
        ev.pop("rows", None)
        summary["eval"] = ev
    with STATE.lock:
        STATE.ds, STATE.decisions, STATE.summary = ds, decisions, summary
    return summary


# ------------------------------------------------------------------ analyst reviews (audit log)


def load_reviews(hit_id: str | None = None) -> list[dict[str, Any]]:
    if not REVIEWS.exists():
        return []
    out = []
    for i, line in enumerate(REVIEWS.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            row.setdefault("seq", i)
            if hit_id is None or row.get("hit_id") == hit_id:
                out.append(row)
    return out


def reviews_by_hit() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in load_reviews():
        out[r["hit_id"]].append(r)
    return out


def case_status(d: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the case stands in the human loop.

    AUTOMATED        engine decided on solid evidence, no person needed (QA sample only)
    OPEN             needs a person, nobody has reviewed it yet
    AWAITING_SECOND  4-eyes: BORDERLINE tier, or an analyst cleared a hit the engine did not clear;
                     a second, different analyst must reach the same decision
    CLOSED           reviewed (and second-checked where required)
    """
    if not reviews:
        status = "AUTOMATED" if d["tier"] in AUTOMATED_TIERS else "OPEN"
        return {"status": status, "final_decision": d["decision"] if status == "AUTOMATED" else None,
                "analysts": []}
    last = reviews[-1]
    latest: dict[str, str] = {}
    for r in reviews:
        latest[(r.get("analyst") or "").strip().lower()] = r["decision"]
    agreeing = sorted(a for a, dec in latest.items() if dec == last["decision"])
    clears = last["decision"] == "DIFFERENT_ENTITY" and d["decision"] != "DIFFERENT_ENTITY"
    needs_two = d["tier"] == "BORDERLINE" or clears
    status = "CLOSED" if not needs_two or len(agreeing) >= 2 else "AWAITING_SECOND"
    return {"status": status, "final_decision": last["decision"], "analysts": agreeing,
            "four_eyes": needs_two}


# ------------------------------------------------------------------ repro badge


def read_repro() -> dict[str, Any]:
    path = REPRO_DIR / "report.json"
    report = None
    at = None
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        except (OSError, ValueError):
            report = None
    return {"report": report, "at": at, "running": STATE.repro_running, "error": STATE.repro_error}


def _run_repro() -> None:
    try:
        proc = subprocess.run(
            [sys.executable, str(SYSTEM / "screen.py"), "repro", "--data", DATA_DIR.as_posix(),
             "--runs", str(REPRO_RUNS), "--out-dir", REPRO_DIR.as_posix(), "--json"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        STATE.repro_error = None if proc.returncode in (0, 1) else (proc.stderr or proc.stdout)[-500:]
    except Exception as exc:  # surfaced in the UI, never crashes the server
        STATE.repro_error = str(exc)
    finally:
        STATE.repro_running = False


# ------------------------------------------------------------------ app


def _startup() -> None:
    """Screen every hit in the background so /api/health answers (with progress) while it runs."""
    STATE.status, STATE.error, STATE.started_at = "loading", None, time.time()
    try:
        compute()
        STATE.status = "ready"
    except Exception as exc:  # surfaced by /api/health and the UI, never crashes the server
        STATE.status, STATE.error = "error", f"{type(exc).__name__}: {exc}"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_startup, name="engine-startup", daemon=True).start()
    yield


app = FastAPI(title="Screening dashboard", version=__version__, lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Readiness: `ready` turns true once both engines have screened every hit (~70 s on dev)."""
    ready = STATE.ds is not None and bool(STATE.summary)
    done, total = STATE.progress
    return {
        "ok": ready,
        "ready": ready,
        "status": "ready" if ready else STATE.status,
        "stage": None if ready else STATE.stage,
        "progress": None if ready else {"done": done, "total": total},
        "elapsed_seconds": round(time.time() - STATE.started_at, 1),
        "error": STATE.error,
        "engine_version": __version__,
        "engine_mode": STATE.summary.get("engines", {}).get("mode") if ready else None,
        "data": DATA_DIR.as_posix(),
    }


def _require_ready() -> None:
    if STATE.ds is None or not STATE.summary:
        detail = STATE.error or f"engine still loading ({STATE.stage}); poll /api/health"
        raise HTTPException(503, detail)


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    _require_ready()
    by_hit = reviews_by_hit()
    statuses = Counter(case_status(d, by_hit.get(h, []))["status"] for h, d in STATE.decisions.items())
    return {**STATE.summary, "reviewed_hits": len(by_hit), "case_status": dict(sorted(statuses.items())),
            "repro": read_repro()}


def _review_reason(d: dict[str, Any]) -> str:
    """Why a REVIEW hit needs a person; mirrors tools/scorecard.py review_reason()."""
    s = (d.get("reason") or "").lower()
    if d.get("reason_code") == "AMBIGUOUS_CUSTOMER_RECORD":
        return "AMBIGUOUS_CUSTOMER"
    if "ENGINES_CONFLICT" in d.get("flags", []):
        return "ENGINES_CONFLICT"
    if s.startswith("escalated, not cleared"):
        return "HOFC_POLICY"
    if "no customer record" in s or "did not lead" in s:
        return "NO_CUSTOMER_RECORD"
    if "date of birth" in s:
        return "SOP_NO_DOB"
    return "OTHER"


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    """Read-only headline aggregates for the demo landing (same definitions as tools/scorecard.py)."""
    _require_ready()
    ds = STATE.ds
    assert ds is not None
    decs = STATE.decisions
    n = len(decs)
    dec = Counter(d["decision"] for d in decs.values())
    tiers = Counter(d["tier"] for d in decs.values())
    reasons = Counter(_review_reason(d) for d in decs.values() if d["decision"] == "REVIEW")
    by_tier_reason: dict[str, Counter[str]] = defaultdict(Counter)
    for d in decs.values():
        by_tier_reason[d["tier"]][d["reason_code"]] += 1
    alerts_closed = sum(
        1 for a in ds.alerts.values()
        if a.hit_ids and all(decs.get(h, {}).get("decision") == "DIFFERENT_ENTITY" for h in a.hit_ids if h in ds.hits)
    )
    disagree = sum("ENGINES_DISAGREE" in d["flags"] for d in decs.values())
    conflict = sum("ENGINES_CONFLICT" in d["flags"] for d in decs.values())

    def pct(a: int) -> float:
        return round(100 * a / n, 1) if n else 0.0

    auto = tiers.get("AUTO_CLEAR", 0) + tiers.get("AUTO_ESCALATE", 0)
    return {
        "hits": n,
        "alerts": len(ds.alerts),
        "alerts_fully_cleared": alerts_closed,
        "tiers": dict(tiers),
        "decisions": dict(dec),
        "fully_automated_pct": pct(auto),
        "decided_by_engine_pct": pct(dec.get("DIFFERENT_ENTITY", 0) + dec.get("SAME_ENTITY", 0)),
        "cleared_pct": pct(dec.get("DIFFERENT_ENTITY", 0)),
        "engine_agreement_pct": pct(n - disagree - conflict),
        "engines_disagree": disagree,
        "engines_conflict": conflict,
        "review_reasons": dict(reasons.most_common()),
        "tier_reason_codes": {t: dict(c.most_common()) for t, c in by_tier_reason.items()},
        "run_seconds": STATE.summary.get("run_seconds"),
        "load_seconds": STATE.summary.get("load_seconds"),
        "dataset": STATE.summary.get("dataset"),
    }


def _row(d: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    ds = STATE.ds
    hit = ds.hits.get(d["hit_id"]) if ds else None
    alert = ds.alerts.get(d["alert_id"]) if ds else None
    cs = case_status(d, reviews)
    eng = d.get("engines") or {}
    return {
        "hit_id": d["hit_id"],
        "alert_id": d["alert_id"],
        "decision": d["decision"],
        "reason_code": d["reason_code"],
        "tier": d["tier"],
        "customer_record_id": d["customer_record_id"],
        "hit_name": hit.name if hit else None,
        "customer_name": alert.customer_name if alert else None,
        "flags": d["flags"],
        "review": reviews[-1]["decision"] if reviews else None,
        "status": cs["status"],
        "final_decision": cs["final_decision"],
        "agreement": eng.get("agreement"),
        "core_decision": (eng.get("core") or {}).get("decision"),
        "check_decision": (eng.get("check") or {}).get("decision"),
        "reviewer_action": bool(eng.get("reviewer_action")),
    }


@app.get("/api/decisions")
def decisions(tier: str | None = None, decision: str | None = None, q: str | None = None,
              engine_flag: str | None = None) -> list[dict]:
    """Queue rows; `engine_flag` = ENGINES_DISAGREE | ENGINES_CONFLICT | ESCALATED_BY_POLICY."""
    _require_ready()
    if engine_flag and engine_flag not in ENGINE_FLAGS:
        raise HTTPException(422, f"engine_flag must be one of {list(ENGINE_FLAGS)}")
    ql = (q or "").strip().lower()
    by_hit = reviews_by_hit()
    rows = []
    for d in STATE.decisions.values():
        if tier and d["tier"] != tier:
            continue
        if decision and d["decision"] != decision:
            continue
        if engine_flag and engine_flag not in d["flags"]:
            continue
        row = _row(d, by_hit.get(d["hit_id"], []))
        if ql and ql not in " ".join(str(v) for v in row.values() if v).lower():
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["hit_id"])
    return rows


@app.get("/api/hits/{hit_id}")
def hit_detail(hit_id: str) -> dict[str, Any]:
    _require_ready()
    ds = STATE.ds
    d = STATE.decisions.get(hit_id)
    if ds is None or d is None:
        raise HTTPException(404, f"unknown hit {hit_id}")
    hit = ds.hits[hit_id]
    alert = ds.alerts.get(hit.alert_id)
    resolution = None
    if alert is not None:
        res = resolve_customer(ds, alert)
        resolution = {
            "method": res.method,
            "path": list(res.path),
            "ambiguous": res.ambiguous,
            "persons": [
                {
                    "key": p.key,
                    "record_ids": list(p.record_ids),
                    "records": [_record(r) for r in p.records],
                    "kyc": [_record(k) for k in p.kyc],
                    "kyc_ids": [k.check_id for k in p.kyc],
                    "note_ids": [n.note_id for n in p.notes],
                    "notes": [_record(n) for n in p.notes],
                }
                for p in res.persons
            ],
        }
    label = ds.practice_labels.get(hit_id)
    reviews = load_reviews(hit_id)
    return {
        "decision": d,
        "hit": _record(hit),
        "alert": _record(alert),
        "resolution": resolution,
        "practice_label": _record(label),
        "reviews": reviews,
        "case": case_status(d, reviews),
    }


@app.get("/api/records/{record_id}")
def record(record_id: str) -> dict[str, Any]:
    """Read-only lookup of any record id cited as evidence."""
    _require_ready()
    ds = STATE.ds
    assert ds is not None
    rid = record_id.strip()
    tables = (
        ("customer", ds.customers),
        ("kyc_check", ds.kyc_checks),
        ("note", ds.notes),
        ("search_result", ds.search_results),
        ("hit", ds.hits),
        ("alert", ds.alerts),
    )
    for kind, table in tables:
        if rid in table:
            return {"id": rid, "kind": kind, "record": _record(table[rid])}
    if rid in ds.accounts_by_id:
        accts = ds.accounts_by_id[rid]
        return {"id": rid, "kind": "account", "record": _record(accts[0]),
                "holders": [a.customer_id for a in accts]}
    if rid in ds.kyc_by_applicant:
        checks = sorted(ds.kyc_by_applicant[rid], key=lambda k: (k.checked_at or "", k.check_id))
        return {"id": rid, "kind": "kyc_applicant", "record": {"applicant_id": rid,
                "check_ids": [k.check_id for k in checks]}}
    raise HTTPException(404, f"no record with id {rid}")


class Review(BaseModel):
    decision: str  # CONFIRM | SAME_ENTITY | DIFFERENT_ENTITY | REVIEW
    note: str = ""
    analyst: str = ""


@app.post("/api/hits/{hit_id}/review")
def review(hit_id: str, body: Review) -> dict[str, Any]:
    _require_ready()
    d = STATE.decisions.get(hit_id)
    if d is None:
        raise HTTPException(404, f"unknown hit {hit_id}")
    if body.decision not in REVIEW_DECISIONS:
        raise HTTPException(422, f"decision must be one of {sorted(REVIEW_DECISIONS)}")
    note, analyst = body.note.strip(), body.analyst.strip()
    if len(note) < MIN_NOTE:
        raise HTTPException(422, "a note explaining the decision is required for every analyst action")
    if not analyst:
        raise HTTPException(422, "analyst name is required")
    final = d["decision"] if body.decision == "CONFIRM" else body.decision
    with STATE.reviews_lock:
        seq = len(load_reviews()) + 1
        row = {
            "seq": seq,
            "hit_id": hit_id,
            "alert_id": d["alert_id"],
            "engine_tier": d["tier"],
            "engine_decision": d["decision"],
            "engine_reason_code": d["reason_code"],
            "action": "confirm" if final == d["decision"] else "override",
            "decision": final,
            "note": note,
            "analyst": analyst,
            "engine_version": __version__,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        REVIEWS.parent.mkdir(parents=True, exist_ok=True)
        with REVIEWS.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {**row, "case": case_status(d, load_reviews(hit_id))}


AUDIT_COLUMNS = ("seq", "at", "analyst", "hit_id", "alert_id", "engine_tier", "engine_decision",
                 "engine_reason_code", "action", "decision", "case_status", "note", "engine_version")


def _csv_safe(v: Any) -> Any:
    """Stop spreadsheet formula injection from free-text notes / names."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _audit_rows() -> list[dict[str, Any]]:
    by_hit = reviews_by_hit()
    out = []
    for r in load_reviews():
        d = STATE.decisions.get(r["hit_id"])
        prior = [x for x in by_hit.get(r["hit_id"], []) if x["seq"] <= r["seq"]]
        status = case_status(d, prior)["status"] if d else None
        out.append({**r, "case_status": status})
    return out


@app.get("/api/reviews")
def reviews() -> list[dict[str, Any]]:
    return list(reversed(_audit_rows()))


@app.get("/api/reviews.csv")
def reviews_csv() -> Response:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=AUDIT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in _audit_rows():
        w.writerow({k: _csv_safe(r.get(k, "")) for k in AUDIT_COLUMNS})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="audit-trail-{stamp}.csv"'})


@app.post("/api/run")
async def rerun() -> dict[str, Any]:
    _require_ready()
    return await asyncio.to_thread(compute)


@app.get("/api/repro")
def repro_status() -> dict[str, Any]:
    return read_repro()


@app.post("/api/repro")
def repro_start() -> dict[str, Any]:
    if not STATE.repro_running:
        STATE.repro_running = True
        STATE.repro_error = None
        threading.Thread(target=_run_repro, daemon=True).start()
    return read_repro()


# ------------------------------------------------------------------ terminal


def _check_writable(value: str) -> str:
    p = Path(value)
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if not any(p == w or w in p.parents for w in (d.resolve() for d in WRITABLE_DIRS)):
        raise ValueError(f"output paths must be under output/ or out/ (got {value})")
    return value


def parse_command(line: str) -> list[str]:
    line = line.strip().replace("\\", "/")
    if SHELL_META.search(line):
        raise ValueError("shell metacharacters are not allowed")
    parts = shlex.split(line, posix=True)
    if len(parts) < 2 or parts[0] != "screen":
        raise ValueError("only `screen <" + "|".join(sorted(ALLOWED_SUBCOMMANDS)) + "> ...` is allowed")
    sub, args = parts[1], parts[2:]
    if sub not in ALLOWED_SUBCOMMANDS and sub not in ("--help", "-h", "--version"):
        raise ValueError(f"subcommand must be one of {sorted(ALLOWED_SUBCOMMANDS)}")
    helping = any(a in ("-h", "--help") for a in args)
    if sub in DATA_SUBCOMMANDS and not helping:
        if not any(a == "--data" or a.startswith("--data=") for a in args):
            args += ["--data", DATA_DIR.as_posix()]
        if sub == "repro":
            for i, a in enumerate(args):
                if a == "--out-dir" and i + 1 < len(args):
                    _check_writable(args[i + 1])
                elif a.startswith("--out-dir="):
                    _check_writable(a.split("=", 1)[1])
    if sub == "run" and not helping:
        positional, i = [], 0
        while i < len(args):
            a = args[i]
            if a == "--trace":
                if i + 1 < len(args):
                    _check_writable(args[i + 1])
                i += 2
                continue
            if a.startswith("--trace="):
                _check_writable(a.split("=", 1)[1])
            elif not a.startswith("-"):
                positional.append(a)
            i += 1
        if not positional:  # `screen run` -> server data, default output file
            args = [DATA_DIR.as_posix(), "output/terminal_run.jsonl", *args]
        elif len(positional) == 1:  # `screen run out.jsonl`
            args = [DATA_DIR.as_posix(), *args]
        out = positional[-1] if len(positional) >= 1 else "output/terminal_run.jsonl"
        _check_writable(out)
    return [sys.executable, str(SYSTEM / "screen.py"), sub, *args]


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":  # repro spawns child processes; kill the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


async def _run_streaming(ws: WebSocket, argv: list[str], recv: asyncio.Future) -> asyncio.Future:
    """Stream a command's output; Ctrl+C (\\x03) from the client or the timeout kills it.

    Uses a thread + Popen (works on any Windows event loop). Returns the pending receive future.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    proc = subprocess.Popen(
        argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    )

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            loop.call_soon_threadsafe(queue.put_nowait, raw.decode("utf-8", "replace"))
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = loop.time() + COMMAND_TIMEOUT
    stopped: str | None = None
    try:
        while True:
            get = asyncio.ensure_future(queue.get())
            wait_for = {get} if stopped else {get, recv}
            timeout = None if stopped else max(0.0, deadline - loop.time())
            done, _ = await asyncio.wait(wait_for, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if get in done:
                item = get.result()
                if item is None:
                    break
                await ws.send_text(item.rstrip("\r\n") + "\r\n")
                continue
            get.cancel()
            if recv in done:
                msg = recv.result()  # raises WebSocketDisconnect if the client left
                recv = asyncio.ensure_future(ws.receive_text())
                if msg == "\x03":
                    stopped = "cancelled (Ctrl+C)"
                    await asyncio.to_thread(_kill_tree, proc)
                continue
            stopped = f"timed out after {int(COMMAND_TIMEOUT)}s - killed"
            await asyncio.to_thread(_kill_tree, proc)
    except BaseException:
        await asyncio.to_thread(_kill_tree, proc)
        raise
    code = await asyncio.to_thread(proc.wait)
    if stopped:
        await ws.send_text(f"\x1b[33m[{stopped}]\x1b[0m\r\n")
    await ws.send_text(f"\x1b[2m[exit {code}]\x1b[0m\r\n\x00done\r\n")
    return recv


@app.websocket("/api/terminal")
async def terminal(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_text(f"screening engine {__version__} - data: {DATA_DIR.as_posix()}\r\n"
                       "allowed: screen run|explain|eval|repro|summary|diff|queue|policy ...  (--engine, eval --compare;"
                       " --data defaults to the server's data;"
                       f" Ctrl+C stops; {int(COMMAND_TIMEOUT)}s limit)\r\n")
    recv: asyncio.Future = asyncio.ensure_future(ws.receive_text())
    try:
        while True:
            line = await recv
            recv = asyncio.ensure_future(ws.receive_text())
            if line == "\x03" or not line.strip():
                await ws.send_text("\x00done\r\n")
                continue
            try:
                argv = parse_command(line)
            except ValueError as exc:
                await ws.send_text(f"\x1b[31mrejected: {exc}\x1b[0m\r\n\x00done\r\n")
                continue
            shown = " ".join(argv[2:])
            await ws.send_text(f"\x1b[2m> screen {shown}\x1b[0m\r\n")
            recv = await _run_streaming(ws, argv, recv)
    except WebSocketDisconnect:
        return
    finally:
        recv.cancel()


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = DIST / path
        if path and target.is_file() and DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
