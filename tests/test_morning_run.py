"""tools/morning_run.py: deterministic report, previous-run comparison and regression detection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import morning_run  # noqa: E402

MINI = ROOT / "tests" / "fixtures" / "mini"


def test_cli_writes_report_and_prints_json(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "morning_run.py"), "--data", str(MINI), "--runs", "2",
         "--out-dir", str(tmp_path), "--date", "2026-01-02"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["healthy"] is True and out["unsafe_clearances"] == 0 and out["byte_identical"] is True
    assert out["issue_title"] == "Morning run 2026-01-02"
    md = (tmp_path / "2026-01-02.md").read_text(encoding="utf-8")
    assert md.startswith("## Morning run 2026-01-02")
    assert "| unsafe clearances | 0 |" in md and "byte-identical (2 runs) | yes" in md
    assert (tmp_path / "2026-01-02.json").exists()


def test_regressions_and_previous(tmp_path):
    base, prev = morning_run.build_report(MINI, 2, "2026-01-02", tmp_path, 5, repro=False)
    assert prev is None and base["healthy"]
    # yesterday scored higher, and today has an unsafe clearance + a non-identical repro
    (tmp_path / "2026-01-01.json").write_text(json.dumps(
        {"date": "2026-01-01", "data_label": MINI.name, "engine": {"eval": {"decision_score": 0.9}}}))
    (tmp_path / "2025-12-31.json").write_text(json.dumps({"date": "2025-12-31", "data_label": "other"}))
    found = morning_run.previous_report(tmp_path, "2026-01-02", MINI.name)
    assert found["date"] == "2026-01-01"
    assert morning_run.previous_report(tmp_path, "2026-01-02", "other")["date"] == "2025-12-31"

    report = json.loads(json.dumps(base))
    report["engine"]["eval"].update(unsafe_clearances=1, unsafe_hits=["H-9"])
    report["repro"] = {"byte_identical": False, "unstable_hits": ["H-9"]}
    regs = morning_run.find_regressions(report, found)
    assert any("unsafe clearance" in r and "H-9" in r for r in regs)
    assert any("not byte-identical" in r for r in regs)
    assert any("decision score dropped 0.9" in r for r in regs)
    assert "### Regressions" in morning_run.render_markdown({**report, "regressions": regs}, found)
