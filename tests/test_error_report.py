"""tools/error_report.py: per-label diffs, risk scan and stage diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "tests" / "fixtures" / "mini"
sys.path.insert(0, str(ROOT / "tools"))

import error_report  # noqa: E402
from screening.contracts import (  # noqa: E402
    Decision,
    Facet,
    HitDecision,
    PracticeLabel,
    ReasonCode,
    RuleResult,
    Signal,
    Tier,
)


def test_cli_writes_markdown_and_json(tmp_path):
    out = tmp_path / "r.md"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "error_report.py"), "--data", str(MINI), "--out", str(out), "--json"],
        capture_output=True, text=True, check=True,
    )
    rep = json.loads(proc.stdout)
    assert rep["score"]["labelled_hits"] == 2
    assert set(rep) >= {"score", "failure_counts", "label_diffs", "risk", "stages"}
    md = out.read_text(encoding="utf-8")
    for heading in ("## 1. Score summary", "## 2. Practice-label failures", "## 3. Risk scan", "## 4. Stage"):
        assert heading in md


def test_build_report_on_mini_matches_score():
    rep = error_report.build_report(MINI)
    assert rep["score"]["unsafe_clearances"] == rep["failure_counts"]["unsafe_clearance"]
    diff_ids = {d["hit_id"] for d in rep["label_diffs"]}
    assert diff_ids <= {"H-1", "H-2"}
    for d in rep["label_diffs"]:
        assert d["primary"] == d["kinds"][0]
        assert set(d["evidence_missing"]) <= set(d["truth_evidence"])
    st = rep["stages"]
    assert st["hits"] == 3
    assert sum(r["alerts"] for r in st["resolution_by_method"].values()) == 2


def _label(**kw):
    base = {"hit_id": "H-9", "alert_id": "A-9", "decision": "SAME_ENTITY",
            "reason_codes": "NAME_DOB_MATCH", "customer_record_ids": "CUS-1", "evidence": "CUS-1; KYC-1"}
    base.update(kw)
    return PracticeLabel.from_raw(base)


def test_classify_failure_types():
    lab = _label()
    assert error_report.classify("SAME_ENTITY", {"decision": "DIFFERENT_ENTITY"}, lab)[0] == "unsafe_clearance"
    assert error_report.classify("SAME_ENTITY", {"decision": "REVIEW"}, lab)[0] == "missed_confirm"
    assert error_report.classify("DIFFERENT_ENTITY", {"decision": "REVIEW"}, lab)[0] == "missed_clear"
    kinds = error_report.classify("SAME_ENTITY", {"decision": "SAME_ENTITY", "reason_code": "NAME_LOCATION_MATCH",
                                                   "customer_record_id": "CUS-2", "evidence": ["CUS-1"]}, lab)
    assert kinds == ["wrong_reason", "wrong_customer", "evidence_mismatch"]
    ok = {"decision": "SAME_ENTITY", "reason_code": "NAME_DOB_MATCH", "customer_record_id": "CUS-1",
          "evidence": ["KYC-1", "CUS-1"]}
    assert error_report.classify("SAME_ENTITY", ok, lab) == []


def _rr(facet, signal, reason=None, weak=False, weak_why=None):
    return RuleResult(rule_id=f"P6.{facet.value}", facet=facet, signal=signal, summary=f"{facet.value} {signal.value}",
                      reason=reason, evidence=("H-9",), weak=weak, weak_why=weak_why)


def _decision(hit_id, trace, flags=(), cid="CUS-1"):
    return HitDecision(hit_id=hit_id, alert_id="A-9", decision=Decision.DIFFERENT_ENTITY,
                       reason_code=ReasonCode.LOCATION_MISMATCH, customer_record_id=cid, reason="x",
                       evidence=("H-9",), tier=Tier.BORDERLINE if flags else Tier.AUTO_CLEAR,
                       flags=tuple(flags), trace=tuple(trace))


def test_risk_scan_flags_dangerous_clearances_only_on_unlabelled():
    loc = _rr(Facet.LOCATION, Signal.CONTRADICTION, ReasonCode.LOCATION_MISMATCH)
    risky = _decision("H-9", [loc, _rr(Facet.NAME, Signal.AGREEMENT),
                              _rr(Facet.DOB, Signal.AGREEMENT, weak=True, weak_why="DOB_FROM_SEARCH_RESULT")],
                      flags=("DOB_FROM_SEARCH_RESULT",))
    clean = _decision("H-8", [loc])
    labelled = _decision("H-7", [loc, _rr(Facet.NAME, Signal.AGREEMENT), _rr(Facet.DOB, Signal.AGREEMENT)])
    ambiguous = _decision("H-6", [loc])
    resolutions = {"A-9": SimpleNamespace(method="unlinked", persons=(object(),))}
    decisions = {d.hit_id: d for d in (risky, clean, labelled)}
    rep = error_report.risk_scan(decisions, {"H-7": _label(hit_id="H-7")}, resolutions)
    assert [c["hit_id"] for c in rep["candidates"]] == ["H-9"]
    dangers = rep["candidates"][0]["dangers"]
    assert "NAME_AND_DOB_AGREE_IN_TRACE" in dangers
    assert any(d.startswith("WEAK:") for d in dangers)
    assert rep["candidates"][0]["deciding"] == ["P6.location: location contradiction"]
    assert rep["by_tier"] == {"AUTO_CLEAR": 2, "BORDERLINE": 1}

    two = {"A-9": SimpleNamespace(method="unlinked", persons=(object(), object()))}
    rep2 = error_report.risk_scan({"H-6": ambiguous}, {}, two)
    assert rep2["candidates"][0]["dangers"] == ["AMBIGUOUS_CUSTOMER(2)"]
    md = error_report.render_markdown({**error_report.build_report(MINI), "risk": rep2})
    assert "H-6" in md and "AMBIGUOUS_CUSTOMER(2)" in md
