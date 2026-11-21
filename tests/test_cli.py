"""CLI commands against tests/fixtures/mini via main([...]); checks exit codes and JSON shapes."""

import json

import pytest

from screening import cli
from screening.cli import main, reviewer_action

FIXTURE = "tests/fixtures/mini"


@pytest.fixture(autouse=True)
def _repo_cwd(monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.delenv("SCREEN_ENGINE", raising=False)


def _json(capsys, argv, code=0):
    assert main(argv) == code
    return json.loads(capsys.readouterr().out)


def test_run_reports_engine_tiers_and_agreement(capsys, tmp_path):
    out = _json(capsys, ["run", FIXTURE, str(tmp_path / "o.jsonl"), "--json"])
    assert out["engine"] == "combined" and out["hits"] == 3
    assert set(out["engine_agreement"]) == {"agree", "disagree", "conflict"}
    assert sum(out["engine_agreement"].values()) == 3
    assert sum(out["tiers"].values()) == 3
    for mode in ("core", "check"):
        out = _json(capsys, ["run", FIXTURE, str(tmp_path / f"{mode}.jsonl"), "--json", "--engine", mode])
        assert out["engine"] == mode and out["engine_agreement"] is None


def test_explain_json_shape_is_backward_compatible_and_extended(capsys):
    x = _json(capsys, ["explain", "H-2", "--data", FIXTURE, "--json"])
    for key in ("hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "reason", "evidence",
                "tier", "flags", "trace"):
        assert key in x
    assert x["decision"] == "SAME_ENTITY" and x["engine"] == "combined"
    assert x["agreement"] in {"AGREE", "DISAGREE", "CONFLICT", "N/A"}
    assert set(x["core"]) >= {"decision", "reason_code", "reason", "reviewer_action", "escalated_by_policy"}
    assert set(x["check"]) >= {"decision", "reason_code", "flags", "facets"}
    assert all(f["vs_core"] in {"supports", "contradicts", "neutral"} for f in x["check"]["facets"])
    assert x["resolution"]["method"] == "account_ref" and x["resolution"]["path"]
    assert "P7" in x["policies"] and set(x["policy_titles"]) == set(x["policies"])


def test_explain_matches_pipeline_in_every_engine(capsys, mini):
    from screening.pipeline import run

    for mode in ("combined", "core", "check"):
        expected = {d.hit_id: d.full() for d in run(mini, mode)}
        for hit_id in sorted(mini.hits):
            x = _json(capsys, ["explain", hit_id, "--data", FIXTURE, "--json", "--engine", mode])
            assert {k: x[k] for k in expected[hit_id]} == expected[hit_id]


def test_single_hit_core_path_matches_full_core(mini):
    from screening.core import core_results
    from screening.loaders import load_dataset

    fresh = load_dataset(FIXTURE)
    full = core_results(mini)
    assert {h: cli._core_for_hit(fresh, h) for h in sorted(fresh.hits)} == full


def test_explain_unknown_hit_exits_2(capsys):
    assert main(["explain", "H-NOPE", "--data", FIXTURE]) == 2


def test_diff_shape_and_pair_filter(capsys):
    d = _json(capsys, ["diff", "--data", FIXTURE, "--json"])
    assert set(d) == {"hits", "status", "pairs", "rows"}
    assert sum(d["pairs"].values()) == d["hits"] == 3
    assert all(":" in k for k in d["pairs"])
    d = _json(capsys, ["diff", "--data", FIXTURE, "--json", "--pair", "D:R"])
    assert d["pair_filter"] == "DIFFERENT_ENTITY:REVIEW"
    assert all(r["core"] == "DIFFERENT_ENTITY" and r["check"] == "REVIEW" for r in d["rows"])


def test_eval_compare_scores_three_engines(capsys):
    e = _json(capsys, ["eval", "--data", FIXTURE, "--compare", "--json"])
    assert e["selected"] == "combined"
    assert set(e["engines"]) == {"core", "check", "combined"}
    for m in e["engines"].values():
        assert "rows" not in m and m["labelled_hits"] == 2 and m["unsafe_clearances"] == 0


def test_eval_plain_still_works(capsys):
    e = _json(capsys, ["eval", "--data", FIXTURE, "--json"])
    assert e["unsafe_clearances"] == 0 and "decision_score" in e


def test_queue_shape_and_limit(capsys):
    q = _json(capsys, ["queue", "--tier", "auto_clear", "--data", FIXTURE, "--json", "--limit", "1"])
    assert q["tier"] == "AUTO_CLEAR" and q["shown"] <= 1 <= q["total"]
    item = q["items"][0]
    assert set(item) == {"hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "flags", "why",
                         "reviewer_action"}
    assert item["why"]


def test_policy_lists_registry_and_filters(capsys):
    p = _json(capsys, ["policy", "--json"])
    ids = [r["id"] for r in p["policies"]]
    assert ids[:9] == [f"P{i}" for i in range(1, 10)] and "HOFC-9" in ids and "DC-2" in ids
    p = _json(capsys, ["policy", "dc-1", "--json"])
    assert [r["id"] for r in p["policies"]] == ["DC-1"]
    assert main(["policy", "NOPE"]) == 2


def test_summary_json(capsys):
    s = _json(capsys, ["summary", "--data", FIXTURE, "--json"])
    assert "data" in s


def test_stress_runs_perturbations_against_our_engine(capsys):
    if cli._load_stress_module() is None:
        pytest.skip("contrib stress test not present")
    s = _json(capsys, ["stress", "--data", FIXTURE, "--json", "--kinds", "dates_long", "name_order"])
    assert s["baseline"]["hits"] == 3 and set(s["kinds"]) == {"dates_long", "name_order"}
    assert s["total_unsafe"] == 0
    for r in s["kinds"].values():
        assert set(r) == {"flips", "unsafe", "error"}
    assert main(["stress", "--data", FIXTURE, "--kinds", "bogus"]) == 2


def test_reviewer_action_extraction():
    r = ("Escalated, not cleared: the customer's verified legal name A B (KYC-1) shares only one name part. "
         "Reviewer: compare the given names, date of birth and nationality against the listing. First-pass x.")
    assert reviewer_action(r) == "compare the given names, date of birth and nationality against the listing."
    assert reviewer_action("The hit is left for a person to review.") is None


# --------------------------------------------------------------------------- colour / presentation


@pytest.fixture
def plain(monkeypatch):
    for var in ("SCREEN_COLOR", "FORCE_COLOR", "NO_COLOR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def color(monkeypatch, plain):
    monkeypatch.setenv("SCREEN_COLOR", "1")


def _out(capsys, argv, code=0):
    assert main(argv) == code
    return capsys.readouterr()


def test_color_switch_env(monkeypatch, plain):
    assert cli.color_enabled() is False  # pytest's captured stdout is not a TTY
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert cli.color_enabled() is True
    monkeypatch.setenv("SCREEN_COLOR", "0")
    assert cli.color_enabled() is False
    monkeypatch.setenv("SCREEN_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli.color_enabled() is True  # an explicit SCREEN_COLOR wins


def test_explain_plain_output_is_unchanged_without_colour(capsys, plain):
    out = _out(capsys, ["explain", "H-2", "--data", FIXTURE]).out
    assert "\x1b[" not in out
    lines = out.splitlines()
    assert lines[0] == "H-2 (alert A-1) -> SAME_ENTITY / NAME_DOB_LOCATION_MATCH   [engine combined]"
    assert lines[1] == "  tier: AUTO_ESCALATE   flags: -   agreement: AGREE"
    assert lines[2].startswith("  customer: CUS-1   evidence: ")
    assert any(line.startswith("  resolution: account_ref -> CUS-1") for line in lines)
    assert lines[-1].startswith("  policies: P4 (Verified beats declared)")


def test_explain_colour_case_card(capsys, color):
    out = _out(capsys, ["explain", "H-2", "--data", FIXTURE]).out
    assert "\x1b[" in out
    text = cli.strip_ansi(out)
    lines = [line.strip() for line in text.splitlines()]
    assert lines[0].startswith("H-2  alert A-1") and lines[0].endswith("engine combined")
    verdict = next(line for line in lines if line.startswith("●"))
    assert "MATCH → compliance" in verdict and "SAME_ENTITY / NAME_DOB_LOCATION_MATCH" in verdict
    assert "AUTO_ESCALATE" in verdict and "✓ AGREE" in verdict
    for heading in ("WHY", "CORE vs CHECK", "RULE TRACE", "RESOLUTION PATH", "EVIDENCE", "POLICIES"):
        assert any(line.startswith(heading) for line in lines), heading
    assert any(line.startswith("├─ ✓ P6.dob") and "agreement" in line and "supports" in line for line in lines)
    assert any(line.startswith("P7") and "Confirm only on agreement" in line for line in lines)
    assert max(len(line) for line in text.splitlines()) <= 100


def test_render_explain_matches_cli_colour_output(capsys, color, mini):
    """The dashboard renders explain in-process from its loaded dataset: it must equal the CLI's output."""
    from screening.loaders import load_dataset

    out = _out(capsys, ["explain", "H-2", "--data", FIXTURE]).out
    assert cli.explain_text(load_dataset(FIXTURE), "H-2", "combined") + "\n" == out
    x = cli.explain_hit(mini, "H-2", "combined")
    assert cli.strip_ansi(cli.render_explain(x)) == cli.render_explain(x, color=False)


def test_json_output_identical_with_colour(capsys, monkeypatch, plain):
    argvs = [["explain", "H-2", "--data", FIXTURE, "--json"], ["eval", "--data", FIXTURE, "--json"],
             ["queue", "--tier", "AUTO_CLEAR", "--data", FIXTURE, "--json"], ["policy", "--json"],
             ["diff", "--data", FIXTURE, "--json"]]
    before = [_out(capsys, a) for a in argvs]
    monkeypatch.setenv("SCREEN_COLOR", "1")
    after = [_out(capsys, a) for a in argvs]
    for b, a in zip(before, after, strict=True):
        assert a.out == b.out and "\x1b[" not in a.out


def test_run_files_unchanged_by_colour(capsys, monkeypatch, plain, tmp_path):
    import hashlib

    _out(capsys, ["run", FIXTURE, str(tmp_path / "plain.jsonl"), "--trace", str(tmp_path / "plain_t.jsonl")])
    monkeypatch.setenv("SCREEN_COLOR", "1")
    res = _out(capsys, ["run", FIXTURE, str(tmp_path / "color.jsonl"), "--trace", str(tmp_path / "color_t.jsonl")])
    assert (tmp_path / "plain.jsonl").read_bytes() == (tmp_path / "color.jsonl").read_bytes()
    assert (tmp_path / "plain_t.jsonl").read_bytes() == (tmp_path / "color_t.jsonl").read_bytes()
    text = cli.strip_ansi(res.out)
    assert "AUTO_CLEAR" in text and "HUMAN_REVIEW" in text and "sha256" in text
    assert hashlib.sha256((tmp_path / "color.jsonl").read_bytes()).hexdigest()[:16] in text
    assert "\x1b[" not in (tmp_path / "color.jsonl").read_text(encoding="utf-8")


def test_run_plain_summary_unchanged(capsys, plain, tmp_path):
    lines = _out(capsys, ["run", FIXTURE, str(tmp_path / "o.jsonl")]).out.splitlines()
    assert "                            engine: combined" in lines
    assert "                              hits: 3" in lines
    assert not any("\x1b[" in line for line in lines)


def test_eval_colour_scorecard(capsys, color):
    text = cli.strip_ansi(_out(capsys, ["eval", "--data", FIXTURE]).out)
    assert text.lstrip().startswith("SCORECARD")
    assert "✓ no real match was ever cleared" in text and "decision score" in text
    text = cli.strip_ansi(_out(capsys, ["eval", "--data", FIXTURE, "--compare"]).out)
    assert "core vs check vs combined" in text and "✓ 0 unsafe clearances" in text
