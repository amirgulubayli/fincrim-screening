#!/usr/bin/env python3
"""fincrim-screening MCP server: the screening engine as read-only tools for Grok.

Python standard library only. JSON-RPC 2.0 over stdio, one JSON message per line
(MCP stdio transport). Implements initialize, ping, tools/list and tools/call.

The engine in `system/` decides; this server only reports what it decided. No tool
changes a decision, writes to the data folder, or edits code. An LLM using these
tools narrates engine output, it never overrides it.

Environment:
  SCREEN_DATA   data folder (default data/dev; relative paths resolve against the repo root)
  SCREEN_REPO   repo root (default: the repo containing this file, else the cwd's)
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

SERVER_NAME = "fincrim-screening"
SERVER_VERSION = "0.2.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
TIERS = ("AUTO_CLEAR", "AUTO_ESCALATE", "BORDERLINE", "HUMAN_REVIEW")
INSTRUCTIONS = (
    "Read-only tools over a deterministic sanctions-screening engine. The engine's decision, "
    "reason code, tier and evidence are final: report them exactly and never suggest a different "
    "decision. Quote hit, customer and evidence IDs from tool output only; if a fact is not in the "
    "output, say it is not recorded rather than guessing. Two independent engines run (dual control): "
    "the decision core decides and an independent rule check runs alongside; when screen_explain "
    "returns a reviewer_action, always state it verbatim as the action for the human reviewer."
)

def find_repo() -> Path:
    """$SCREEN_REPO, else the repo this file lives in, else the nearest parent of the cwd with the engine.

    The cwd fallback covers `grok plugin install ./.grok/plugins/fincrim-screening --trust`, which copies
    the plugin out of the repo; Grok starts MCP servers from the project directory.
    """
    if os.environ.get("SCREEN_REPO"):
        return Path(os.environ["SCREEN_REPO"]).resolve()
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    for base in (*here.parents, cwd, *cwd.parents):
        if (base / "system" / "screening" / "pipeline.py").is_file():
            return base
    return cwd


REPO = find_repo()
SYSTEM = REPO / "system"
if str(SYSTEM) not in sys.path:
    sys.path.insert(0, str(SYSTEM))


class ToolError(Exception):
    """A problem the caller can fix (bad argument, missing data folder)."""


# ------------------------------------------------------------------ engine access

def _engine():
    try:
        from screening import __version__
        from screening.evaluate import score
        from screening.loaders import load_dataset
        from screening.pipeline import adjudicate, run
    except ImportError as exc:  # pragma: no cover - only when the repo layout is wrong
        raise ToolError(f"cannot import the engine from {SYSTEM}: {exc}") from exc
    return __version__, load_dataset, run, adjudicate, score


def data_dir(override: str | None = None) -> Path:
    raw = override or os.environ.get("SCREEN_DATA") or "data/dev"
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    path = path.resolve()
    if not path.is_dir():
        raise ToolError(f"data folder not found: {path} (set SCREEN_DATA)")
    return path


def _signature(path: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in path.glob("*.jsonl")))


_CACHE: dict[str, Any] = {}


def _load(override: str | None):
    """Dataset + decisions for the data folder, recomputed when any input file changes."""
    version, load_dataset, run, adjudicate, score = _engine()
    path = data_dir(override)
    key = (str(path), _signature(path))
    if _CACHE.get("key") != key:
        ds = load_dataset(path)
        decisions, core, checks = _run_dual(ds)
        _CACHE.clear()
        _CACHE.update(key=key, ds=ds, decisions=decisions, core=core, checks=checks, path=path,
                      version=version)
    return _CACHE


# ------------------------------------------------------------------ dual control
# The engine runs two independent engines (pipeline.py): the decision core decides, the modular
# rule check runs alongside, and compiler.merge_with_core combines them. The merged decision keeps
# the core's row as trace[0] but not the check's own verdict, so we record each check verdict as the
# pipeline computes it (one pass, decisions identical to `screen run`).

ENGINES_CONFLICT = "ENGINES_CONFLICT"
ENGINES_DISAGREE = "ENGINES_DISAGREE"
ESCALATED_BY_POLICY = "ESCALATED_BY_POLICY"
_REVIEWER = re.compile(r"Reviewer:\s*(.+?)(?=\s+Core:\s|$)", re.S)

# Head of Financial Crime policy calls (contrib/aml-bot/README.md), used when the engine has no
# system/screening/policy.py with a POLICIES table. Paraphrased; the core engine implements them.
BUILTIN_POLICIES = (
    ("HOFC-1", "One shared name part", "Only one name part is shared with the verified legal name: clear only "
     "when the given names are full and clearly differ; otherwise REVIEW ('Escalated, not cleared ... Reviewer: ...')."),
    ("HOFC-2", "Patronymic surname", "A shared surname that is commonly a father's given name (e.g. Umar, Ahmed, "
     "Ibrahim): REVIEW, because name order is unreliable."),
    ("HOFC-3", "Former single states", "Countries that were one state (former Yugoslavia, USSR, Czechoslovakia, "
     "Sudan/South Sudan, Ethiopia/Eritrea): REVIEW, not a location clearance."),
    ("HOFC-4", "Business address only", "An individual hit whose only country is a business address: REVIEW."),
    ("HOFC-5", "Several customers, all clear", "Several customers fit and all lead to clearance: clear with "
     "customer AMBIGUOUS."),
    ("HOFC-6", "Ages in news text", "Ages inside news-report paragraphs are ignored (safe direction)."),
    ("HOFC-7", "Countries in notes", "All countries in customer notes count for the location rule."),
    ("HOFC-8", "Possible name reorder", "A possible name reorder is left in REVIEW rather than tuned to a label."),
)


def _run_dual(ds):
    from screening import pipeline

    core: dict[str, dict[str, Any]] = {}
    if pipeline.engine_mode() in ("combined", "core"):
        from screening.core import core_results

        core = core_results(ds)  # cached on ds, reused by pipeline.run
    checks: dict[str, Any] = {}
    original = pipeline.check_hit

    def recording(ds_, hit_id):
        d = original(ds_, hit_id)
        checks[hit_id] = d
        return d

    pipeline.check_hit = recording
    try:
        decisions = pipeline.run(ds)
    finally:
        pipeline.check_hit = original
    return decisions, core, checks


def reviewer_action(*reasons: str | None) -> str | None:
    """The human-in-the-loop instruction the engine wrote after 'Reviewer:', or None."""
    for text in reasons:
        m = _REVIEWER.search(text or "")
        if m:
            return m.group(1).strip()
    return None


def _agreement(core_dec: str | None, check_dec: str | None, flags) -> str:
    if ENGINES_CONFLICT in flags:
        return "CONFLICT"
    if not core_dec or not check_dec:
        return "UNAVAILABLE"
    if core_dec == check_dec:
        return "AGREE"
    if core_dec == "DIFFERENT_ENTITY" and check_dec == "SAME_ENTITY":
        return "CONFLICT"
    return "DISAGREE"


def _dual(c: dict[str, Any], d) -> dict[str, Any]:
    """Core verdict, check verdict, agreement status and reviewer action for one merged decision."""
    core = c["core"].get(d.hit_id)
    check = c["checks"].get(d.hit_id)
    core_v = None if not core else {
        "decision": core.get("decision"), "reason_code": core.get("reason_code"),
        "reason": core.get("reason") or "", "evidence": [str(e) for e in core.get("evidence") or () if e],
        "customer_record_id": core.get("customer_record_id"),
    }
    check_v = None if check is None else {
        "decision": check.decision.value, "reason_code": check.reason_code.value, "reason": check.reason,
        "evidence": list(check.evidence), "customer_record_id": check.customer_record_id,
        "flags": list(check.flags),
    }
    return {
        "core_verdict": core_v,
        "check_verdict": check_v,
        "agreement": _agreement(core_v and core_v["decision"], check_v and check_v["decision"], d.flags),
        "escalated_by_policy": ESCALATED_BY_POLICY in d.flags,
        "reviewer_action": reviewer_action(d.reason, core_v["reason"] if core_v else None),
    }


# ------------------------------------------------------------------ tools

def screen_summary(args: dict[str, Any]) -> dict[str, Any]:
    c = _load(args.get("data"))
    return {"engine_version": c["version"], "data": str(c["path"]), **c["ds"].summary()}


def screen_run(args: dict[str, Any]) -> dict[str, Any]:
    c = _load(args.get("data"))
    decisions = c["decisions"]
    flags = Counter(f for d in decisions for f in d.flags)
    return {
        "engine_version": c["version"],
        "data": str(c["path"]),
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d.decision.value for d in decisions).items())),
        "tiers": dict(sorted(Counter(d.tier.value for d in decisions).items())),
        "decision_by_tier": {
            f"{dec}/{tier}": n
            for (dec, tier), n in sorted(Counter((d.decision.value, d.tier.value) for d in decisions).items())
        },
        "reason_codes": dict(sorted(Counter(d.reason_code.value for d in decisions).items())),
        "flags": dict(sorted(flags.items())),
    }


def screen_explain(args: dict[str, Any]) -> dict[str, Any]:
    hit_id = str(args.get("hit_id") or "").strip()
    if not hit_id:
        raise ToolError("hit_id is required")
    c = _load(args.get("data"))
    for d in c["decisions"]:
        if d.hit_id.lower() == hit_id.lower():
            return {"engine_version": c["version"], **d.full(), **_dual(c, d), "policies": _policies_for(d)}
    raise ToolError(f"unknown hit {hit_id}")


def engine_diff(args: dict[str, Any]) -> dict[str, Any]:
    """Dual control: counts by (core decision, check decision) pair and the hits where they differ."""
    c = _load(args.get("data"))
    want = str(args.get("agreement") or "").upper()
    if want and want not in ("AGREE", "DISAGREE", "CONFLICT", "UNAVAILABLE"):
        raise ToolError("agreement must be one of AGREE, DISAGREE, CONFLICT, UNAVAILABLE")
    limit = _int(args.get("limit"), 50, 0, 1000)
    pairs: Counter[str] = Counter()
    status: Counter[str] = Counter()
    rows = []
    for d in c["decisions"]:
        x = _dual(c, d)
        core_dec = (x["core_verdict"] or {}).get("decision") or "NONE"
        check_dec = (x["check_verdict"] or {}).get("decision") or "NONE"
        pairs[f"{core_dec}/{check_dec}"] += 1
        status[x["agreement"]] += 1
        if (x["agreement"] == want) if want else (x["agreement"] != "AGREE"):
            rows.append({"hit_id": d.hit_id, "core": core_dec, "check": check_dec,
                         "final_decision": d.decision.value, "final_reason_code": d.reason_code.value,
                         "tier": d.tier.value, "agreement": x["agreement"], "flags": list(d.flags),
                         "reviewer_action": x["reviewer_action"]})
    compared = sum(n for s, n in status.items() if s != "UNAVAILABLE")
    return {
        "engine_version": c["version"],
        "hits": len(c["decisions"]),
        "pairs": dict(sorted(pairs.items())),
        "agreement": dict(sorted(status.items())),
        "agreement_rate": round(status.get("AGREE", 0) / compared, 4) if compared else None,
        "escalated_by_policy": sum(1 for d in c["decisions"] if ESCALATED_BY_POLICY in d.flags),
        "listed": want or "not AGREE",
        "total": len(rows),
        "shown": min(limit, len(rows)),
        "list": rows[:limit],
    }


def policy_list(args: dict[str, Any]) -> dict[str, Any]:
    """Policy registry from system/screening/policy.py (registry() / POLICIES), else the built-in HoFC calls."""
    kind = str(args.get("kind") or "").strip().lower()
    table = None
    try:
        from screening import policy as policy_mod  # type: ignore[attr-defined]

        if callable(getattr(policy_mod, "registry", None)):
            table = policy_mod.registry()
        else:
            table = policy_mod.POLICIES
    except (ImportError, AttributeError):
        table = None
    if table is not None:
        items = list(table.items()) if isinstance(table, dict) else list(table)
        policies = []
        for i, p in enumerate(items, 1):
            if isinstance(p, tuple) and len(p) == 2 and isinstance(p[0], str):  # dict item (id, value)
                pid, val = p
                p = {"id": pid, **val} if isinstance(val, dict) else {"id": pid, "text": val}
            elif not isinstance(p, dict):
                p = {"id": str(i), "text": p}
            policies.append(json.loads(json.dumps(p, default=str)))
        if kind:
            policies = [p for p in policies if str(p.get("kind", "")).lower() == kind]
        return {"source": "system/screening/policy.py", "count": len(policies), "policies": policies}
    builtin = [{"id": i, "kind": "hofc", "title": t, "effect": x} for i, t, x in BUILTIN_POLICIES]
    if kind:
        builtin = [p for p in builtin if p["kind"] == kind]
    return {
        "source": "built-in (contrib/aml-bot/README.md Head of Financial Crime policy calls)",
        "count": len(builtin),
        "policies": builtin,
    }


def _policies_for(d) -> list[str] | None:
    """Policy ids that apply to one decision, when the engine has screening.policy.policies_for."""
    try:
        from screening.policy import policies_for  # type: ignore[attr-defined]
    except ImportError:
        return None
    return list(policies_for(d))


def review_queue(args: dict[str, Any]) -> dict[str, Any]:
    tier = str(args.get("tier") or "HUMAN_REVIEW").upper()
    if tier not in TIERS:
        raise ToolError(f"tier must be one of {', '.join(TIERS)}")
    limit = _int(args.get("limit"), 20, 1, 500)
    c = _load(args.get("data"))
    rows = [d for d in c["decisions"] if d.tier.value == tier]
    return {
        "tier": tier,
        "total": len(rows),
        "shown": min(limit, len(rows)),
        "hits": [
            {"hit_id": d.hit_id, "alert_id": d.alert_id, "decision": d.decision.value,
             "reason_code": d.reason_code.value, "customer_record_id": d.customer_record_id,
             "flags": list(d.flags), "reason": d.reason}
            for d in rows[:limit]
        ],
    }


def screen_eval(args: dict[str, Any]) -> dict[str, Any]:
    *_, score = _engine()
    c = _load(args.get("data"))
    if not c["ds"].practice_labels:
        raise ToolError("no practice_labels.jsonl in the data folder")
    result = score([d.submission() for d in c["decisions"]], c["ds"].practice_labels)
    rows = result.pop("rows")
    wrong = [r for r in rows if not (r["correct"] and r["reason_ok"] and r["customer_ok"])]
    limit = _int(args.get("limit"), 25, 0, 1000)
    return {
        "engine_version": c["version"],
        **result,
        "unsafe_hits": [r["hit_id"] for r in rows if r["unsafe"]],
        "mismatches_total": len(wrong),
        "mismatches": wrong[:limit],
    }


def screen_repro(args: dict[str, Any]) -> dict[str, Any]:
    runs = _int(args.get("runs"), 5, 2, 10)
    path = data_dir(args.get("data"))
    with tempfile.TemporaryDirectory(prefix="screen-repro-") as tmp:
        proc = subprocess.run(
            [sys.executable, str(SYSTEM / "screen.py"), "repro", "--data", str(path),
             "--runs", str(runs), "--out-dir", tmp, "--json"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), timeout=1800,
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"repro failed (exit {proc.returncode}): {proc.stderr.strip()[-800:]}") from exc
    return {"data": str(path), "exit_code": proc.returncode, **report}


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"expected an integer, got {value!r}") from exc
    return max(lo, min(hi, n))


DATA_PROP = {"data": {"type": "string",
                      "description": "Optional data folder; defaults to $SCREEN_DATA or data/dev."}}

TOOLS: dict[str, tuple[Any, dict[str, Any]]] = {
    "screen_summary": (screen_summary, {
        "description": "Dataset size: counts of alerts, hits, customers, accounts, KYC checks, notes, "
                       "search results and practice labels in the data folder.",
        "inputSchema": {"type": "object", "properties": {**DATA_PROP}},
    }),
    "screen_run": (screen_run, {
        "description": "Run the deterministic engine over every hit and return counts by decision "
                       "(DIFFERENT_ENTITY / SAME_ENTITY / REVIEW), by human-in-the-loop tier "
                       "(AUTO_CLEAR / AUTO_ESCALATE / BORDERLINE / HUMAN_REVIEW), reason code and flag.",
        "inputSchema": {"type": "object", "properties": {**DATA_PROP}},
    }),
    "screen_explain": (screen_explain, {
        "description": "Full evidence trail for one screening hit: final decision, reason code, customer "
                       "record, reason text, evidence IDs, tier, flags and the trace (trace[0] = the decision "
                       "core, rule_id CORE.aml; the rest = the independent rule check, one line per facet). "
                       "Also core_verdict and check_verdict (the two independent engines), agreement "
                       "(AGREE / DISAGREE / CONFLICT / UNAVAILABLE), escalated_by_policy and reviewer_action "
                       "(the 'Reviewer: ...' instruction for the human, or null). Use this to answer 'why "
                       "was H-xxx cleared/escalated?'.",
        "inputSchema": {"type": "object", "required": ["hit_id"],
                        "properties": {"hit_id": {"type": "string", "description": "Hit ID, e.g. H-0001"},
                                       **DATA_PROP}},
    }),
    "review_queue": (review_queue, {
        "description": "Hits in one human-in-the-loop tier, sorted by hit ID: HUMAN_REVIEW (analyst "
                       "queue), BORDERLINE (automated but rests on weak facts, 4-eyes QA), AUTO_ESCALATE "
                       "or AUTO_CLEAR.",
        "inputSchema": {"type": "object", "properties": {
            "tier": {"type": "string", "enum": list(TIERS), "default": "HUMAN_REVIEW"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
            **DATA_PROP}},
    }),
    "screen_eval": (screen_eval, {
        "description": "Score the engine against practice labels with the organisers' cost model: "
                       "decision score, accuracy, review rate, unsafe clearances (a true SAME/REVIEW "
                       "hit cleared, cost 60) with their hit IDs, and the mismatching rows.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 25,
                      "description": "Max mismatching rows to return."},
            **DATA_PROP}},
    }),
    "engine_diff": (engine_diff, {
        "description": "Dual control overview: how often the decision core and the independent rule check "
                       "agree, counts by core/check decision pair, agreement rate, hits escalated by policy, "
                       "and the list of hits where they differ (or those with the given agreement status). "
                       "The core decides; CONFLICT = core cleared but the check confirms, so the hit goes to REVIEW.",
        "inputSchema": {"type": "object", "properties": {
            "agreement": {"type": "string", "enum": ["AGREE", "DISAGREE", "CONFLICT", "UNAVAILABLE"],
                          "description": "List only hits with this status (default: every hit that is not AGREE)."},
            "limit": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 50},
            **DATA_PROP}},
    }),
    "policy_list": (policy_list, {
        "description": "The policy registry the decisions rest on: SOP principles (P1..P9), Head of Financial "
                       "Crime calls (HOFC-*, the core's cautious 'Escalated, not cleared' cases) and dual-control "
                       "merge rules (DC-*), from system/screening/policy.py; falls back to a built-in HoFC list "
                       "from contrib/aml-bot/README.md if that module is missing.",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["sop", "hofc", "dual_control"],
                     "description": "Only policies of this kind (default: all)."}}},
    }),
    "screen_repro": (screen_repro, {
        "description": "Determinism proof: run the engine N times in fresh processes with different "
                       "PYTHONHASHSEED values and report each output's sha256, byte_identical and any "
                       "unstable hits.",
        "inputSchema": {"type": "object", "properties": {
            "runs": {"type": "integer", "minimum": 2, "maximum": 10, "default": 5}, **DATA_PROP}},
    }),
}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise KeyError(name)
    func = TOOLS[name][0]
    try:
        with contextlib.redirect_stdout(sys.stderr):  # stdout belongs to JSON-RPC
            payload = func(arguments or {})
        is_error = False
    except ToolError as exc:
        payload, is_error = {"error": str(exc)}, True
    except Exception as exc:  # noqa: BLE001 - report, never crash the server
        traceback.print_exc(file=sys.stderr)
        payload, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": is_error,
    }


# ------------------------------------------------------------------ JSON-RPC

def handle(msg: Any) -> dict[str, Any] | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
    is_notification = "id" not in msg
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {
            "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, **spec} for n, (_, spec) in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return None if is_notification else _error(mid, -32602, f"Unknown tool: {name}")
        result = call_tool(name, params.get("arguments") or {})
    elif method.startswith("notifications/"):
        return None
    else:
        return None if is_notification else _error(mid, -32601, f"Method not found: {method}")
    return None if is_notification else {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> int:
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = _error(None, -32700, "Parse error")
        else:
            reply = handle(msg)
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
