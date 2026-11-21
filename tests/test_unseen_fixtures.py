"""Unseen-style fixtures (tests/fixtures/unseen, built by make_unseen.py): new KYC-note wording,
date formats absent from dev, countries spelled differently, an unreadable hit DOB, and a BOM +
junk line in notes.jsonl. Every case is a true or plausible match, so nothing may be cleared."""

from pathlib import Path

import pytest

from screening.contracts import Decision
from screening.loaders import LOAD_WARNINGS, load_dataset, read_jsonl
from screening.pipeline import run

UNSEEN = Path(__file__).parent / "fixtures" / "unseen"
CASES = ["H-U1", "H-U2", "H-U3", "H-U4", "H-U5"]


@pytest.fixture(scope="module")
def unseen():
    return load_dataset(UNSEEN)


def test_bom_and_junk_lines_are_skipped_not_fatal(unseen):
    assert "NOTE-U2" in unseen.notes  # the BOM did not hide the first row
    assert len(unseen.notes) == 1
    assert any("notes.jsonl" in w and "skipped" in w for w in LOAD_WARNINGS)


def test_non_object_lines_are_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('﻿{"a": 1}\n[1, 2]\n"text"\n{broken\n\n{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}]


@pytest.mark.parametrize("mode", ["combined", "check"])
def test_every_unseen_case_answered(unseen, mode):
    assert sorted(d.hit_id for d in run(unseen, mode)) == CASES


@pytest.mark.parametrize("mode", ["combined", "core", "check"])
@pytest.mark.parametrize("hit_id", CASES)
def test_uncertain_unseen_data_is_never_cleared(unseen, mode, hit_id):
    d = {x.hit_id: x for x in run(unseen, mode)}[hit_id]
    assert d.decision is not Decision.DIFFERENT_ENTITY, f"{mode} cleared {hit_id}: {d.reason}"
