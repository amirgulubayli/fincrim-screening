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
