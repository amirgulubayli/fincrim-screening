"""Name normalisation and comparison (SOP principle 8).

Implemented: name_parts() tokenisation.
TODO (seed issue "Name matcher"): parts_match() and names_agree().
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PLACEHOLDERS = frozenset({"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbc"})
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_NON_WORD = re.compile(r"[^0-9a-z]+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_parts(
    name: str | None, *, company: bool = False, legal_forms: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Distinct comparable parts of a name, in first-seen order.

    Ignores case, accents, punctuation and anything in brackets. Single letters
    are initials (not parts). Placeholders and digits are not parts. For
    companies the legal forms from name_variants.json are not parts.
    """
    if not name:
        return ()
    text = strip_accents(name).lower()
    text = _BRACKETS.sub(" ", text)
    text = text.replace("'", "").replace("’", "")
    out: list[str] = []
    for token in _NON_WORD.sub(" ", text).split():
        if len(token) < 2 or token.isdigit() or token in PLACEHOLDERS:
            continue
        if company and token in legal_forms:
            continue
        if token not in out:
            out.append(token)
    return tuple(out)


@dataclass(frozen=True)
class NameMatch:
    agrees: bool
    matched: tuple[tuple[str, str], ...]  # (customer part, hit part) pairs
    hit_name: str  # which hit name / alias agreed (for the reason text)
    fuzzy: bool  # True if any pair matched only by edit distance -> weak evidence


def parts_match(a: str, b: str, variants: dict | None = None) -> bool:
    """SOP 8: identical, listed together in name_variants.json, or within edit
    distance (1 for 4-6 letters, 2 for longer; <4 letters must be identical;
    the shorter part's length decides). Joined/split parts are handled in
    names_agree()."""
    raise NotImplementedError("seed issue: Name matcher (SOP principle 8)")


def names_agree(customer_name: str, hit_names: list[str], variants: dict | None = None):
    """Return NameMatch for the best agreeing hit name/alias, or None.

    Names agree when >= 2 parts match, or every part of the shorter name matches.
    """
    raise NotImplementedError("seed issue: Name matcher (SOP principle 8)")
