"""Demo dashboard API (not scored). Wraps the stdlib engine under system/.

Run from the repo root:  uv run uvicorn app.server.main:app --port 8000
Data folder comes from env SCREEN_DATA (default data/dev).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import shlex
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))

from screening import __version__  # noqa: E402
from screening.evaluate import score  # noqa: E402
from screening.loaders import Dataset, load_dataset  # noqa: E402
from screening.pipeline import run as run_pipeline  # noqa: E402
from screening.resolve import resolve_customer  # noqa: E402

DATA_DIR = Path(os.environ.get("SCREEN_DATA", "data/dev"))
REVIEWS = ROOT / "out" / "reviews.jsonl"
DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
ALLOWED_SUBCOMMANDS = {"run", "explain", "eval", "repro", "summary"}
SHELL_META = re.compile(r"[;&|`$<>()\\{}\[\]*?!~\n\r\"']")


class State:
    ds: Dataset | None = None
    decisions: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {}
    lock = threading.Lock()


STATE = State()


def _record(rec: Any) -> dict[str, Any] | None:
    if rec is None:
        return None
    d = dataclasses.asdict(rec)
    extra = d.pop("extra", None) or {}
    return {**d, **({"extra": extra} if extra else {})}


def compute() -> dict[str, Any]:
    t0 = time.perf_counter()
    ds = load_dataset(DATA_DIR)
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    results = run_pipeline(ds)
    run_s = time.perf_counter() - t1
    decisions = {d.hit_id: d.full() for d in results}
    summary: dict[str, Any] = {
        "engine_version": __version__,
        "data": str(DATA_DIR),
        "dataset": ds.summary(),
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d["decision"] for d in decisions.values()).items())),
        "tiers": dict(sorted(Counter(d["tier"] for d in decisions.values()).items())),
        "load_seconds": round(load_s, 2),
        "run_seconds": round(run_s, 2),
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "eval": None,
    }
    if ds.practice_labels:
        ev = score([{k: v for k, v in d.items() if k not in ("tier", "flags", "trace")}
                    for d in decisions.values()], ds.practice_labels)
        ev.pop("rows", None)
        summary["eval"] = ev
    with STATE.lock:
        STATE.ds, STATE.decisions, STATE.summary = ds, decisions, summary
    return summary


def load_reviews(hit_id: str | None = None) -> list[dict[str, Any]]:
    if not REVIEWS.exists():
        return []
    out = []
    for line in REVIEWS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if hit_id is None or row.get("hit_id") == hit_id:
                out.append(row)
    return out


app = FastAPI(title="Screening dashboard", version=__version__)


@app.on_event("startup")
async def _startup() -> None:
    await asyncio.to_thread(compute)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": STATE.ds is not None, "engine_version": __version__, "data": str(DATA_DIR)}


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    reviewed = {r["hit_id"] for r in load_reviews()}
    return {**STATE.summary, "reviewed_hits": len(reviewed)}


@app.get("/api/decisions")
def decisions(tier: str | None = None, decision: str | None = None, q: str | None = None) -> list[dict]:
    ds = STATE.ds
    ql = (q or "").strip().lower()
    reviewed = {r["hit_id"]: r for r in load_reviews()}
    rows = []
    for d in STATE.decisions.values():
        if tier and d["tier"] != tier:
            continue
        if decision and d["decision"] != decision:
            continue
        hit = ds.hits.get(d["hit_id"]) if ds else None
        alert = ds.alerts.get(d["alert_id"]) if ds else None
        row = {
            "hit_id": d["hit_id"],
            "alert_id": d["alert_id"],
            "decision": d["decision"],
            "reason_code": d["reason_code"],
            "tier": d["tier"],
            "customer_record_id": d["customer_record_id"],
            "hit_name": hit.name if hit else None,
            "customer_name": alert.customer_name if alert else None,
            "flags": d["flags"],
            "review": reviewed.get(d["hit_id"], {}).get("decision"),
        }
        if ql and ql not in " ".join(str(v) for v in row.values() if v).lower():
            continue
        rows.append(row)
    return rows


@app.get("/api/hits/{hit_id}")
def hit_detail(hit_id: str) -> dict[str, Any]:
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
    return {
        "decision": d,
        "hit": _record(hit),
        "alert": _record(alert),
        "resolution": resolution,
        "practice_label": _record(label),
        "reviews": load_reviews(hit_id),
    }


class Review(BaseModel):
    decision: str  # CONFIRM | SAME_ENTITY | DIFFERENT_ENTITY | REVIEW
    note: str = ""
    analyst: str = "analyst"


@app.post("/api/hits/{hit_id}/review")
def review(hit_id: str, body: Review) -> dict[str, Any]:
    d = STATE.decisions.get(hit_id)
    if d is None:
        raise HTTPException(404, f"unknown hit {hit_id}")
    allowed = {"CONFIRM", "SAME_ENTITY", "DIFFERENT_ENTITY", "REVIEW"}
    if body.decision not in allowed:
        raise HTTPException(422, f"decision must be one of {sorted(allowed)}")
    final = d["decision"] if body.decision == "CONFIRM" else body.decision
    row = {
        "hit_id": hit_id,
        "alert_id": d["alert_id"],
        "engine_decision": d["decision"],
        "engine_reason_code": d["reason_code"],
        "action": "confirm" if body.decision == "CONFIRM" or final == d["decision"] else "override",
        "decision": final,
        "note": body.note,
        "analyst": body.analyst or "analyst",
        "engine_version": __version__,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REVIEWS.parent.mkdir(parents=True, exist_ok=True)
    with REVIEWS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


@app.post("/api/run")
async def rerun() -> dict[str, Any]:
    return await asyncio.to_thread(compute)


def parse_command(line: str) -> list[str]:
    line = line.strip()
    if SHELL_META.search(line):
        raise ValueError("shell metacharacters are not allowed")
    parts = shlex.split(line, posix=True)
    if len(parts) < 2 or parts[0] != "screen":
        raise ValueError("only `screen <run|explain|eval|repro|summary> ...` is allowed")
    if parts[1] not in ALLOWED_SUBCOMMANDS and parts[1] not in ("--help", "-h", "--version"):
        raise ValueError(f"subcommand must be one of {sorted(ALLOWED_SUBCOMMANDS)}")
    return [sys.executable, str(SYSTEM / "screen.py"), *parts[1:]]


@app.websocket("/api/terminal")
async def terminal(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_text(f"screening engine {__version__} - data: {DATA_DIR}\r\n"
                       "allowed: screen run|explain|eval|repro|summary ...\r\n")
    try:
        while True:
            line = await ws.receive_text()
            if not line.strip():
                await ws.send_text("\x00done\r\n")
                continue
            try:
                argv = parse_command(line)
            except ValueError as exc:
                await ws.send_text(f"\x1b[31mrejected: {exc}\x1b[0m\r\n\x00done\r\n")
                continue
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(ROOT),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            )
            assert proc.stdout is not None
            async for raw in proc.stdout:
                await ws.send_text(raw.decode("utf-8", "replace").rstrip("\r\n") + "\r\n")
            code = await proc.wait()
            await ws.send_text(f"\x1b[2m[exit {code}]\x1b[0m\r\n\x00done\r\n")
    except WebSocketDisconnect:
        return


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = DIST / path
        if path and target.is_file() and DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
