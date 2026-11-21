"""Unit tests for pure helpers in tools/task.py and tools/bootstrap_github.py (no network)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


task = _load("task")
boot = _load("bootstrap_github")


@pytest.mark.parametrize("title,expected", [
    ("Engine: add fuzzy name matching", "engine-add-fuzzy-name-matching"),
    ("One two three four five six seven", "one-two-three-four-five"),
    ("  Fix   CSV / XLSX import!! ", "fix-csv-xlsx-import"),
    ("!!!", "task"),
])
def test_slugify(title, expected):
    assert task.slugify(title) == expected


def test_branch_name():
    assert task.branch_name(12, "Dashboard: Show hits") == "12-dashboard-show-hits"


@pytest.mark.parametrize("branch,expected", [
    ("12-fix-parser", 12), ("feature/7-x", 7), ("main", None),
    ("setup/collab-tooling", None), ("v2-thing", None), ("", None),
])
def test_issue_number_from_branch(branch, expected):
    assert task.issue_number_from_branch(branch) == expected


def test_label_parsing():
    labels = [{"name": "area:engine"}, {"name": "status:claimed"}, "priority:p0", {"name": "bug"}]
    assert task.label_names(labels) == ["area:engine", "status:claimed", "priority:p0", "bug"]
    assert task.label_value(labels, "area") == "engine"
    assert task.label_value(labels, "priority") == "p0"
    assert task.label_value(labels, "agent") is None
    assert task.status_of(labels) == "claimed"
    assert task.status_of([]) == "-"
    assert task.logins([{"login": "a"}, "b"]) == ["a", "b"]


def test_seed_example_valid_and_ordered():
    items = boot.load_seed(TOOLS / "seed_issues.example.json")
    order = [i["key"] for i in boot.topo_order(items)]
    assert order.index("engine-fuzzy-names") < order.index("dashboard-match-explain")


def test_seed_missing_file(tmp_path):
    assert boot.load_seed(tmp_path / "nope.json") == []


def test_seed_cycle(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([
        {"key": "a", "title": "A", "depends_on": ["b"]},
        {"key": "b", "title": "B", "depends_on": ["a"]},
    ]))
    with pytest.raises(boot.BootstrapError):
        boot.topo_order(boot.load_seed(p))


def test_cli_help_parses():
    with pytest.raises(SystemExit) as e:
        task.build_parser().parse_args(["--help"])
    assert e.value.code == 0
