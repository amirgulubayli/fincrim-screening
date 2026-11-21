"""The Grok plugin's MCP server speaks JSON-RPC 2.0 / MCP over stdio and wraps the engine read-only."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / ".grok" / "plugins" / "fincrim-screening" / "mcp" / "server.py"
MINI = ROOT / "tests" / "fixtures" / "mini"
EXPECTED_TOOLS = {"screen_summary", "screen_run", "screen_explain", "review_queue", "screen_eval", "screen_repro",
                  "engine_diff", "policy_list"}


def _module():
    spec = importlib.util.spec_from_file_location("fincrim_mcp_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session(messages: list[dict]) -> list[dict]:
    env = dict(os.environ, SCREEN_DATA=str(MINI))
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run([sys.executable, str(SERVER)], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _req(mid: int, method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_initialize_list_and_call():
    replies = _session([
        _req(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "pytest", "version": "0"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _req(2, "tools/list", {}),
        _req(3, "tools/call", {"name": "screen_summary", "arguments": {}}),
    ])
    assert [r["id"] for r in replies] == [1, 2, 3]  # the notification gets no reply
    assert all(r["jsonrpc"] == "2.0" and "result" in r for r in replies)

    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert "tools" in init["capabilities"]
    assert init["serverInfo"]["name"] == "fincrim-screening"

    tools = replies[1]["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"

    call = replies[2]["result"]
    assert call["isError"] is False
    assert call["content"][0]["type"] == "text"
    summary = json.loads(call["content"][0]["text"])
    assert summary == call["structuredContent"]
    assert summary["hits"] == 3 and summary["alerts"] == 2 and summary["practice_labels"] == 2


def test_tools_wrap_the_engine_read_only():
    replies = _session([
        _req(1, "tools/call", {"name": "screen_run", "arguments": {}}),
        _req(2, "tools/call", {"name": "screen_explain", "arguments": {"hit_id": "H-2"}}),
        _req(3, "tools/call", {"name": "review_queue", "arguments": {"tier": "HUMAN_REVIEW", "limit": 5}}),
        _req(4, "tools/call", {"name": "screen_eval", "arguments": {}}),
    ])
    run, explain, queue, ev = (r["result"]["structuredContent"] for r in replies)
    assert run["hits"] == 3 and sum(run["tiers"].values()) == 3
    assert explain["hit_id"] == "H-2"
    assert {"decision", "reason_code", "reason", "evidence", "tier", "flags", "trace"} <= explain.keys()
    assert queue["tier"] == "HUMAN_REVIEW" and queue["total"] == len(queue["hits"])
    assert ev["labelled_hits"] == 2 and ev["unsafe_clearances"] == 0 and ev["unsafe_hits"] == []


def test_errors_are_well_formed():
    replies = _session([
        _req(1, "tools/call", {"name": "screen_explain", "arguments": {"hit_id": "H-NOPE"}}),
        _req(2, "tools/call", {"name": "no_such_tool", "arguments": {}}),
        _req(3, "no/such/method", {}),
        _req(4, "ping"),
    ])
    assert replies[0]["result"]["isError"] is True
    assert "unknown hit" in replies[0]["result"]["structuredContent"]["error"]
    assert replies[1]["error"]["code"] == -32602
    assert replies[2]["error"]["code"] == -32601
    assert replies[3]["result"] == {}


def test_dual_control_tools():
    replies = _session([
        _req(1, "tools/call", {"name": "screen_explain", "arguments": {"hit_id": "H-2"}}),
        _req(2, "tools/call", {"name": "engine_diff", "arguments": {}}),
        _req(3, "tools/call", {"name": "engine_diff", "arguments": {"agreement": "AGREE", "limit": 1}}),
        _req(4, "tools/call", {"name": "policy_list", "arguments": {}}),
        _req(5, "tools/call", {"name": "engine_diff", "arguments": {"agreement": "MAYBE"}}),
    ])
    explain, diff, agree, policies = (r["result"]["structuredContent"] for r in replies[:4])
    # screen_explain: both engines' verdicts, agreement status and the reviewer action
    assert {"core_verdict", "check_verdict", "agreement", "reviewer_action", "escalated_by_policy"} <= explain.keys()
    assert explain["trace"][0]["rule_id"] == "CORE.aml"
    assert explain["core_verdict"]["decision"] == explain["decision"]
    assert explain["agreement"] in {"AGREE", "DISAGREE", "CONFLICT", "UNAVAILABLE"}
    if explain["core_verdict"]["decision"] == explain["check_verdict"]["decision"]:
        assert explain["agreement"] == "AGREE"
    # engine_diff: pair counts cover every hit; default list = hits that are not AGREE
    assert sum(diff["pairs"].values()) == diff["hits"] == 3
    assert sum(diff["agreement"].values()) == 3
    assert diff["total"] == 3 - diff["agreement"].get("AGREE", 0) == len(diff["list"])
    assert agree["shown"] == min(1, agree["total"]) and all(r["agreement"] == "AGREE" for r in agree["list"])
    # policy_list: system/screening/policy.py if it exists, else the built-in HoFC calls
    assert policies["count"] == len(policies["policies"]) > 0
    assert all("id" in p for p in policies["policies"])
    assert any(p["id"].startswith("HOFC-") for p in policies["policies"])
    assert explain["policies"] is None or isinstance(explain["policies"], list)
    assert replies[4]["result"]["isError"] is True


def test_policy_list_kind_filter():
    replies = _session([_req(1, "tools/call", {"name": "policy_list", "arguments": {"kind": "hofc"}})])
    out = replies[0]["result"]["structuredContent"]
    assert out["count"] > 0 and all(p["kind"] == "hofc" for p in out["policies"])


def test_reviewer_action_and_agreement():
    m = _module()
    conflict = ("Escalated, not cleared: the decision core found a contradiction but the independent rule check "
                "confirms the match (NAME_DOB_MATCH). Reviewer: compare the customer's verified identity with the "
                "hit. Core: The customer's DOB differs. Reviewer: something else.")
    assert m.reviewer_action(conflict) == "compare the customer's verified identity with the hit."
    assert m.reviewer_action("Escalated, not cleared: x. Reviewer: read the date and compare it.") == \
        "read the date and compare it."
    assert m.reviewer_action("No instruction here.", None) is None
    assert m.reviewer_action("plain", "Escalated: y. Reviewer: check the listing.") == "check the listing."
    assert m._agreement("SAME_ENTITY", "SAME_ENTITY", ()) == "AGREE"
    assert m._agreement("DIFFERENT_ENTITY", "SAME_ENTITY", ()) == "CONFLICT"
    assert m._agreement("REVIEW", "SAME_ENTITY", ("ENGINES_CONFLICT",)) == "CONFLICT"
    assert m._agreement("REVIEW", "DIFFERENT_ENTITY", ("ENGINES_DISAGREE",)) == "DISAGREE"
    assert m._agreement("REVIEW", None, ()) == "UNAVAILABLE"
