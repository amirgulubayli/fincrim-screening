"""Team patches on the decision core (see system/screening/core/__init__.py)."""

from screening.core import aml_engine as E


def test_names_memoisation_returns_fresh_equal_lists():
    n = E.Names({})
    a = n.parts("Abdul Salam Bello")
    a.append("mutated")
    assert n.parts("Abdul Salam Bello") == ["abdul", "salam", "bello"]
    assert n.agree("Abdul Salam Bello", "Bello, Abdul Salam") is n.agree("Abdul Salam Bello", "Bello, Abdul Salam")


class _D:
    search_by_query: dict = {}


def _profile(notes):
    hit = {"hit_id": "H-T", "entity_type": "Individual", "name": "Doe, John", "profile_notes": notes}
    return E.hit_profile(_D(), hit)


def test_labelled_dob_line_in_first_paragraph_is_read():
    p = _profile("Name: Doe, John\nSex: M\nDOB: 6/20/1955\nHeight: 5'10\"\n\nWanted for: parole violation")
    assert p["dobs"] == [(1955, 6, 20)] and p["dob_src"] == "profile notes"


def test_labelled_dob_outside_first_paragraph_is_ignored():
    p = _profile("Name: Doe, John\n\nDOB: 6/20/1955")
    assert p["dobs"] == []


def test_unreadable_labelled_dob_blocks_instead_of_guessing():
    p = _profile("Name: Doe, John\nDOB: sometime in the fifties")
    assert p["dobs"] == [] and p["dob_unparsed"] == ["sometime in the fifties"]
