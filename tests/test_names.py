"""SOP principle 8 name matching (hand-built names and variant tables only)."""

import json

import pytest

from screening.normalize.names import (
    CANNOT_JUDGE_FEW_PARTS,
    CANNOT_JUDGE_NO_HIT_NAME,
    NameMatch,
    has_enough_parts,
    levenshtein,
    load_variants,
    name_parts,
    names_agree,
    parts_match,
    real_part_count,
)

VARIANTS = {
    "_note": "test table",
    "given_names": [["muhammad", "mohammad", "mohammed", "mohd"], ["abdul", "abdel", "abd"], ["jon", "john"]],
    "company_legal_forms": ["ltd", "limited", "gmbh", "ag", "sa", "llc", "inc", "S.A.R.L."],
}


def agrees(customer, hits, variants=VARIANTS, **kw):
    m = names_agree(customer, hits, variants, **kw)
    return m is not None and m.agrees


# ---------------------------------------------------------------- parts


def test_parts_ignore_case_accents_punctuation_brackets():
    assert name_parts("Jérôme  KERVIEL-Smith (AKA) [old]") == ("jerome", "kerviel", "smith")
    assert name_parts("O'Brien, Seán") == ("obrien", "sean")


def test_initials_and_duplicates_are_not_parts():
    assert name_parts("Carl E. Andersen") == ("carl", "andersen")
    assert name_parts("Ali Ali Khan") == ("ali", "khan")


def test_placeholders_and_digits_are_not_parts():
    assert real_part_count("XXX MARTON XXX") == 1
    assert real_part_count("UNKNOWN NADEEM") == 1
    assert real_part_count("BROOKS N/A") == 1
    assert real_part_count("AZAM NA NA") == 1
    assert real_part_count("RAHMAN TEST TEST") == 1
    assert real_part_count("BUSTAMANTE 0000") == 1
    assert real_part_count("XX Lena XXXX Berg") == 2


def test_alphanumeric_company_part_is_kept():
    assert name_parts("N26 Bank AG", company=True, legal_forms=load_variants(VARIANTS)) == ("n26", "bank")


def test_legal_forms_only_dropped_for_companies():
    v = load_variants(VARIANTS)
    assert name_parts("Acme Trading Ltd", company=True, legal_forms=v) == ("acme", "trading")
    assert name_parts("Acme Trading Ltd") == ("acme", "trading", "ltd")
    assert name_parts("Brel S.A.R.L.", company=True, legal_forms=v) == ("brel",)
    assert name_parts("Brel SARL", company=True, legal_forms=v) == ("brel",)


def test_enough_parts_guard():
    assert not has_enough_parts("XXX MARTON XXX")
    assert has_enough_parts("Lena Berg")
    assert has_enough_parts("Acme Ltd", company=True, variants=VARIANTS)
    assert not has_enough_parts("Ltd", company=True, variants=VARIANTS)


def test_letter_fold_only_when_comparing():
    # search results are filed under the historical key; comparisons fold the stroke-l
    assert name_parts("Tomasz Paweł") == ("tomasz", "pawe")
    assert name_parts("Tomasz Paweł", fold=True) == ("tomasz", "pawel")
    assert parts_match("Paweł", "pawel")


# ---------------------------------------------------------------- part comparison


def test_levenshtein_is_plain_not_damerau():
    assert levenshtein("ahmde", "ahmed") == 2
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("nadeem", "nadeem", True),
        ("mohammad", "muhammad", True),  # listed together (and 1 edit)
        ("mohd", "muhammad", True),  # listed together only
        ("abd", "abdel", True),  # listed together, short part
        ("jon", "jan", False),  # <4 letters must be identical
        ("ali", "aly", False),
        ("karl", "carl", True),  # 4 letters: 1 edit
        ("karl", "kalr", False),  # swap = 2 edits
        ("ahmde", "ahmed", False),  # 5 letters: swap = 2 edits > 1
        ("hassan", "hasan", True),  # shorter is 5 -> 1 edit
        ("suleiman", "suleiamn", True),  # 8 letters: swap = 2 edits <= 2
        ("gonzalez", "goznalez", True),
        ("abdullahi", "abdlulahi", True),
        ("christopher", "christophe", True),
        ("stefan", "stefanovic", False),  # shorter (6) decides: 4 edits
        ("bartholomew", "bartolomeo", True),  # 2 edits, 10 letters
        ("bartholomew", "bartolomeu", True),
        ("whitfield", "whitfeld", True),
        ("john", "jonathan", False),
    ],
)
def test_parts_match(a, b, expected):
    assert parts_match(a, b, VARIANTS) is expected
    assert parts_match(b, a, VARIANTS) is expected


def test_parts_match_normalises_input():
    assert parts_match("MÜLLER", "muller")
    assert not parts_match("", "x")


# ---------------------------------------------------------------- name agreement


def test_one_of_two_parts_is_not_agreement():
    assert names_agree("CHINEDU AZEEZ", ["Ridwan, Azeez"], VARIANTS) is None


def test_variant_agreement():
    m = names_agree("Mohammad Nadeem", ["Muhammad Nadeem"], VARIANTS)
    assert isinstance(m, NameMatch) and m.agrees
    assert m.matched == (("mohammad", "muhammad"), ("nadeem", "nadeem"))
    assert m.hit_name == "Muhammad Nadeem"
    assert not m.fuzzy


def test_comma_order_and_case_do_not_matter():
    assert agrees("LENA MARIA BERG", ["Berg, Lena Maria"])


def test_two_matching_parts_agree_even_if_others_differ():
    assert agrees("Lena Maria Berg Olsen", ["Berg, Lena Sofia Dahl"])


def test_every_part_of_shorter_name_matches():
    assert agrees("Lena Berg", ["Lena Sofia Berg Dahl"])
    assert not agrees("Lena Berg Holm", ["Lena Sofia Dahl"])


def test_single_part_hit_alias_can_agree():
    m = names_agree("Carl Andersen", ["Zed Borodin", "Andersen"], VARIANTS)
    assert m is not None and m.agrees and m.hit_name == "Andersen"


def test_joined_and_split_parts_both_ways():
    m = names_agree("Abdul Salam Khan", ["Abdulsalam Khan"], VARIANTS)
    assert m is not None and m.agrees and ("abdul salam", "abdulsalam") in m.matched
    m = names_agree("Abdulsalam Khan", ["Abdul Salam Khan"], VARIANTS)
    assert m is not None and m.agrees and ("abdulsalam", "abdul salam") in m.matched
    # one part covering both counts as ONE matched part
    assert names_agree("Abdulsalam Khan", ["Abdul Salam Rahman"], VARIANTS) is None
    assert agrees("Abdul Salam", ["Abdulsalam"])  # shorter name fully covered


def test_each_part_matched_at_most_once():
    # "nadia" may match "nadja" or "nadia" but only one of them
    assert names_agree("Nadia Hassan", ["Nadia Nadja"], VARIANTS) is None
    # a repeated part counts once: "Omar Omar" is one match, not two
    assert names_agree("Omar Omar Farouk", ["Omar Khan Ali"], VARIANTS) is None
    m = names_agree("Omar Farouk", ["Omar Omar"], VARIANTS)  # hit has ONE distinct part
    assert m is not None and m.matched == (("omar", "omar"),)


def test_one_to_one_assignment_is_maximum_not_greedy():
    # greedy pairing "karla"->"karl" would leave "carl" unmatched; best uses both
    m = names_agree("Karl Karla", ["Carl Karla"], VARIANTS)
    assert m is not None and m.agrees
    assert len(m.matched) == 2


def test_fuzzy_flag_only_when_edit_distance_needed():
    m = names_agree("Suleiamn Umar", ["Umar, Suleiman"], VARIANTS)
    assert m is not None and m.agrees and m.fuzzy
    # prefer an exact alias over a fuzzy primary name
    m = names_agree("Suleiman Umar", ["Suleiamn Umar", "Umar Suleiman"], VARIANTS)
    assert m is not None and not m.fuzzy and m.hit_name == "Umar Suleiman"


def test_transposition_breaks_short_part():
    assert names_agree("Ahmde Khan", ["Ahmed Khan"], {}) is None


def test_placeholder_customer_cannot_be_judged():
    m = names_agree("XXX MARTON XXX", ["Marton Kovacs"], VARIANTS)
    assert m is not None and not m.agrees and not m.judgeable
    assert m.why == CANNOT_JUDGE_FEW_PARTS
    m = names_agree("UNKNOWN NADEEM", ["Imran Qureshi"], VARIANTS)
    assert m is not None and not m.judgeable  # never a clean mismatch


def test_no_comparable_hit_name_cannot_be_judged():
    m = names_agree("Lena Berg", ["", "XXX", "محمد"], VARIANTS)
    assert m is not None and not m.agrees and m.why == CANNOT_JUDGE_NO_HIT_NAME


def test_company_legal_forms_ignored():
    assert agrees("Acme Trading Ltd", ["ACME TRADING LIMITED"], company=True)
    assert agrees("Stadtwerke München GmbH", ["Stadtwerke Munchen AG"], company=True)
    assert names_agree("Acme Ltd", ["Zenith Ltd"], VARIANTS, company=True) is None


def test_company_with_one_part_can_be_judged():
    assert agrees("Acme Ltd", ["ACME LLC"], company=True)


def test_without_legal_list_defaults_still_apply_to_companies():
    assert agrees("Acme Trading Ltd", ["Acme Trading Limited"], variants=None, company=True)


def test_customer_names_may_be_a_list():
    assert agrees(["XXX BERG", "Lena Berg"], ["Berg, Lena"])


def test_deterministic_repeat():
    first = names_agree("Mohammad Abdul Salam", ["Abdulsalam, Muhammad", "Mohd Abdel Salam"], VARIANTS)
    for _ in range(5):
        assert names_agree("Mohammad Abdul Salam", ["Abdulsalam, Muhammad", "Mohd Abdel Salam"], VARIANTS) == first


# ---------------------------------------------------------------- variant loader layouts


def test_loader_accepts_list_of_lists():
    assert parts_match("jon", "john", [["jon", "john"]])


def test_loader_accepts_dict_of_groups():
    v = {"muhammad": ["mohd", "mohammed"], "legal_forms": ["ltd"]}
    assert parts_match("mohd", "mohammed", v)
    assert agrees("Acme Ltd", ["Acme"], variants=v, company=True)


def test_loader_accepts_group_objects_and_containers():
    v = {"groups": [{"canonical": "Yusuf", "variants": ["Youssef", "Yousef"]}]}
    assert parts_match("youssef", "yusuf", v)
    v2 = {"names": {"given": [["Ivan", "Ioann"]]}}
    assert parts_match("ivan", "ioann", v2)


def test_loader_reads_path(tmp_path):
    (tmp_path / "name_variants.json").write_text(json.dumps(VARIANTS), encoding="utf-8")
    assert parts_match("mohd", "muhammad", tmp_path)
    assert parts_match("mohd", "muhammad", str(tmp_path / "name_variants.json"))
    assert load_variants(tmp_path / "missing").groups == {}


def test_loader_caches_per_object():
    assert load_variants(VARIANTS) is load_variants(VARIANTS)
