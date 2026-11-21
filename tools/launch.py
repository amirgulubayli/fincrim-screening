"""One command to bring up the whole system: engine + review dashboard (with the embedded CLI).

    python tools/launch.py up   [--data PATH] [--xlsx WORKBOOK] [--port 8000] [--no-wait]
    python tools/launch.py status
    python tools/launch.py down
    python tools/launch.py doctor          # check prerequisites only

What `up` does (idempotent, safe to re-run):
  1. checks prerequisites (python 3.10+, uv, node/npm)
  2. finds the data folder: --data, else $SCREEN_DATA, else data/dev; if missing and --xlsx is
     given, rebuilds data/dev from the workbook (tools/import_xlsx.py)
  3. installs Python deps (uv sync) and builds the dashboard (npm ci && npm run build) if needed
  4. starts the server detached (uvicorn app.server.main:app) serving the built UI + API on --port
  5. waits until both engines have screened every hit (/api/health ready, ~1-2 min on dev)
     and prints the URL. State lives in out/dashboard.json, logs in out/dashboard.log.

Standard library only, works on Windows / macOS / Linux. Designed to be called by a person,
by the Grok plugin (dashboard_up / dashboard_status / dashboard_down tools) or by a bot.
Prints a JSON summary with --json.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
STATE = OUT / "dashboard.json"
LOG = OUT / "dashboard.log"
WEB = ROOT / "app" / "web"
REQUIRED = ("alerts.jsonl", "hits.jsonl")
IS_WIN = os.name == "nt"


def _say(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, flush=True)


def _exe(name: str) -> str | None:
    return shutil.which(name) or (shutil.which(name + ".cmd") if IS_WIN else None)


def doctor() -> dict:
    checks = {
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 10),
        "uv": _exe("uv"),
        "npm": _exe("npm"),
        "gh": _exe("gh"),
        "grok": _exe("grok"),
    }
    checks["ok"] = bool(checks["python_ok"] and checks["uv"] and checks["npm"])
    return checks


def resolve_data(data: str | None, xlsx: str | None, quiet: bool) -> Path:
    raw = data or os.environ.get("SCREEN_DATA") or "data/dev"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if all((path / f).exists() for f in REQUIRED):
        return path
    if xlsx:
        _say(f"data not found at {path}; rebuilding it from {xlsx} ...", quiet)
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run([_exe("uv") or "uv", "run", "--with", "openpyxl", "python", "tools/import_xlsx.py",
                        xlsx, "--out", str(path)], cwd=ROOT, check=True)
        return path
    raise SystemExit(
        f"No data folder at {path}. Put the data-room files (alerts.jsonl, hits.jsonl, customers.jsonl, ...) "
        "there, pass --data PATH, set SCREEN_DATA, or pass --xlsx \"Screening Dev Set - browse.xlsx\"."
    )


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _health(port: int, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - not up (yet)
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict:
    st = _read_state()
    port = st.get("port", 8000)
    health = _health(port)
    return {
        "running": bool(health) or _alive(st.get("pid")),
        "ready": bool(health and health.get("ready")),
        "url": f"http://127.0.0.1:{port}/" if st else None,
        "pid": st.get("pid"),
        "data": st.get("data"),
        "health": health,
        "log": str(LOG),
    }


def build(quiet: bool, force: bool = False) -> None:
    uv = _exe("uv")
    if not uv:
        raise SystemExit("uv not found: install it from https://docs.astral.sh/uv/ (one line), then re-run.")
    _say("installing Python dependencies (uv sync) ...", quiet)
    subprocess.run([uv, "sync", "--quiet"], cwd=ROOT, check=True)
    if force or not (WEB / "dist" / "index.html").exists():
        npm = _exe("npm")
        if not npm:
            raise SystemExit("npm not found: install Node.js 20+ to build the dashboard, then re-run.")
        _say("building the dashboard (npm ci && npm run build) ...", quiet)
        subprocess.run([npm, "ci", "--silent"], cwd=WEB, check=True)
        subprocess.run([npm, "run", "build", "--silent"], cwd=WEB, check=True)


def up(args: argparse.Namespace) -> dict:
    quiet = args.json
    current = status()
    if current["running"] and _read_state().get("port") == args.port:
        _say(f"already running at {current['url']}", quiet)
        return current
    data = resolve_data(args.data, args.xlsx, quiet)
    build(quiet, force=args.rebuild)
    OUT.mkdir(exist_ok=True)
    env = dict(os.environ, SCREEN_DATA=str(data))
    cmd = [_exe("uv") or "uv", "run", "uvicorn", "app.server.main:app", "--host", args.host,
           "--port", str(args.port)]
    log = LOG.open("w", encoding="utf-8")
    kwargs: dict = {"cwd": ROOT, "env": env, "stdout": log, "stderr": subprocess.STDOUT}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    STATE.write_text(json.dumps({"pid": proc.pid, "port": args.port, "data": str(data),
                                 "started": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    _say(f"server starting (pid {proc.pid}); screening every hit with both engines ...", quiet)
    if not args.no_wait:
        deadline = time.time() + args.timeout
        last = None
        while time.time() < deadline:
            h = _health(args.port)
            if h and h.get("ready"):
                break
            if proc.poll() is not None:
                raise SystemExit(f"server exited early; see {LOG}")
            stage = (h or {}).get("stage")
            if stage != last and stage:
                _say(f"  ... {stage}", quiet)
                last = stage
            time.sleep(3)
    result = status()
    _say(("READY: " if result["ready"] else "STARTED (still loading): ") + f"{result['url']}", quiet)
    return result


def down() -> dict:
    st = _read_state()
    pid = st.get("pid")
    if pid and _alive(pid):
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
    if STATE.exists():
        STATE.unlink()
    return {"stopped": bool(pid), "pid": pid}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up", help="start engine + dashboard")
    p_up.add_argument("--data")
    p_up.add_argument("--xlsx", help="rebuild data/dev from the browse workbook if the data is missing")
    p_up.add_argument("--port", type=int, default=8000)
    p_up.add_argument("--host", default="127.0.0.1")
    p_up.add_argument("--timeout", type=int, default=600, help="seconds to wait for readiness")
    p_up.add_argument("--no-wait", action="store_true")
    p_up.add_argument("--rebuild", action="store_true", help="force a fresh dashboard build")
    for name in ("status", "down", "doctor"):
        sub.add_parser(name)
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = {"up": lambda: up(args), "status": status, "down": down, "doctor": doctor}[args.cmd]()
    if args.json or args.cmd in {"status", "doctor", "down"}:
        print(json.dumps(result, indent=2, default=str))
    return 0 if (args.cmd != "doctor" or result.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
