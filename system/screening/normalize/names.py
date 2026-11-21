"""Name normalisation and comparison (SOP principle 8).

Public API
----------
name_parts(name, company=, legal_forms=)   distinct comparable parts, first-seen order
real_part_count(name, company=, variants=) number of real parts (placeholders excluded)
has_enough_parts(name, company=, variants=) False for an individual with < 2 real parts
load_variants(source)                       compile name_variants.json (dict/list/path), cached
parts_match(a, b, variants)                 SOP 8 part-vs-part test (bool)
names_agree(customer_name, hit_names, variants, company=)
    -> NameMatch(agrees=True, ...)  best agreeing hit name / alias
    -> None                         clean non-agreement (every hit name was judged)
    -> NameMatch(agrees=False, judgeable=False, why=...)  cannot judge on name
       (individual customer with fewer than two real parts, or no comparable
       hit name). Never treat this as a mismatch: rules must neither confirm
       nor clear on it. Always test ``m is None`` for a mismatch and
       ``m is not None and m.agrees`` for agreement.

Matching rules (SOP 8): parts are compared ignoring case, accents, punctuation
and anything in brackets; single letters are initials, not parts; each distinct
part counts once. Two parts match when identical, listed together in
name_variants.json, written together or apart (``Abdulsalam`` ~ ``Abdul Salam``,
one matched part covering both), or within plain Levenshtein distance 1 for
4-6 letters and 2 for 7+ (the shorter part's length decides; shorter than 4
must be identical; a swap of two letters is two edits). Names agree when at
least two parts match or every part of the shorter name matches, with each part
used at most once (exact maximum one-to-one assignment, deterministic order).
Standard library only, deterministic.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PLACEHOLDERS = frozenset(
    {"na", "n/a", "test", "unknown", "unk", "xxx", "none", "null", "nil", "tbc", "tba", "fnu", "lnu"}
)
# Used only when name_variants.json gives no legal-forms list (safer than none:
# "Acme Ltd" vs "Acme Limited" must still agree).
DEFAULT_LEGAL_FORMS = frozenset(
    {
        "ab", "ag", "as", "asa", "bv", "co", "company", "corp", "corporation", "gmbh", "inc",
        "kg", "limited", "llc", "llp", "lp", "ltd", "nv", "oy", "oyj", "plc", "sa", "sarl",
        "sas", "se", "sl", "spa", "srl",
    }
)
_BRACKETS = re.compile(r"[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]")
_NON_WORD = re.compile(r"[^0-9a-z]+")
# Latin letters that NFKD does not decompose.
_FOLD = str.maketrans(
    {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
     "œ": "oe", "Œ": "OE", "ß": "ss", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "ı": "i",
     "ħ": "h", "Ħ": "H"}
)
_MAX_RUN = 3  # a joined part may cover up to 3 consecutive parts on the other side
_NODE_BUDGET = 200_000  # safety net for the exact assignment search


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _raw_tokens(name: str, fold: bool = True) -> list[str]:
    text = strip_accents(name.translate(_FOLD) if fold else name).lower()
    previous = None
    while previous != text:  # nested brackets
        previous, text = text, _BRACKETS.sub(" ", text)
    text = text.replace("'", "").replace("’", "").replace("`", "")
    return _NON_WORD.sub(" ", text).split()


def _norm_key(text: str) -> str:
    """One comparable key for a variant entry / part: accents, case, punctuation removed."""
    return "".join(_raw_tokens(text))


def _is_placeholder(token: str) -> bool:
    return token in PLACEHOLDERS or token.isdigit() or set(token) == {"x"}


# ------------------------------------------------------------------ variants


@dataclass(frozen=True, eq=False)
class Variants:
    """Compiled name_variants.json: variant groups and company legal forms."""

    groups: dict[str, frozenset[int]] = field(default_factory=dict)  # key -> group ids
    legal_forms: frozenset[str] = frozenset()  # single-token forms (and joined multi-token)
    legal_sequences: tuple[tuple[str, ...], ...] = ()  # multi-token forms, longest first
    has_legal_list: bool = False

    def listed_together(self, a: str, b: str) -> bool:
        ga = self.groups.get(a)
        return bool(ga) and not ga.isdisjoint(self.groups.get(b, ()))

    def company_forms(self) -> tuple[frozenset[str], tuple[tuple[str, ...], ...]]:
        if self.has_legal_list:
            return self.legal_forms, self.legal_sequences
        return DEFAULT_LEGAL_FORMS, ()


EMPTY_VARIANTS = Variants()

_LEGAL_KEY = re.compile(r"legal|form|suffix|compan|corporate", re.I)
_CONTAINER_WORDS = ("names", "groups", "variants", "aliases", "nicknames", "given", "family")


def _is_container_key(key: str) -> bool:
    k = key.lower()
    return "_" in k or k.endswith(_CONTAINER_WORDS)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k in sorted(value, key=str):
            out.extend(_strings(value[k]))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend(_strings(v))
        return out
    return []


def _is_flat(value: Any) -> bool:
    """A single group: strings, or a dict whose values are strings / lists of strings."""
    if isinstance(value, (list, tuple)):
        return all(isinstance(v, str) for v in value)
    if isinstance(value, dict):
        return all(isinstance(v, str) or (isinstance(v, (list, tuple)) and _is_flat(v))
                   for v in value.values())
    return False


def _collect_groups(value: Any, key: str | None, groups: list[list[str]]) -> None:
    """Tolerant walk over the variant layouts we know of: a list of lists, a dict
    of groups ({canonical: [variants]}), group objects ({"canonical": .., "variants":
    [..]}), or containers ({"given_names": ...}) of any of those."""
    if isinstance(value, str):
        if key is not None and not _is_container_key(key):
            groups.append([key, value])  # {"mohd": "muhammad"}
        return
    if isinstance(value, dict):
        for k in sorted(value, key=str):
            if not str(k).startswith("_"):
                _collect_groups(value[k], str(k), groups)
        return
    if not isinstance(value, (list, tuple)):
        return
    if _is_flat(value):
        if key is None:
            groups.append(list(value))  # a bare group
        elif not _is_container_key(key):
            groups.append([key, *value])  # {"muhammad": ["mohammed", ...]}
        # a container key holding plain strings: groups unknown, skip
        return
    for v in value:
        if _is_flat(v):
            group = _strings(v)
            if group:
                groups.append(group)
        else:
            _collect_groups(v, None, groups)


def compile_variants(data: Any) -> Variants:
    """Build Variants from the parsed JSON of name_variants.json (any common layout)."""
    if isinstance(data, Variants):
        return data
    groups: list[list[str]] = []
    legal: list[str] = []
    has_legal = False
    if isinstance(data, dict):
        for k in sorted(data, key=str):
            key = str(k)
            if key.startswith("_"):
                continue
            if _LEGAL_KEY.search(key):
                has_legal = True
                legal.extend(_strings(data[k]))
            else:
                _collect_groups(data[k], key, groups)
    elif isinstance(data, (list, tuple)):
        _collect_groups(list(data), None, groups)

    index: dict[str, set[int]] = {}
    for gid, group in enumerate(groups):
        keys = {_norm_key(s) for s in group}
        keys.discard("")
        if len(keys) < 2:
            continue
        for key in keys:
            index.setdefault(key, set()).add(gid)

    singles: set[str] = set()
    sequences: set[tuple[str, ...]] = set()
    for form in legal:
        toks = tuple(_raw_tokens(form))
        if not toks:
            continue
        singles.add("".join(toks))
        if len(toks) > 1:
            sequences.add(toks)
    return Variants(
        groups={k: frozenset(v) for k, v in sorted(index.items())},
        legal_forms=frozenset(singles),
        legal_sequences=tuple(sorted(sequences, key=lambda s: (-len(s), s))),
        has_legal_list=has_legal,
    )


@lru_cache(maxsize=16)
def _load_file(path: str) -> Variants:
    p = Path(path)
    if p.is_dir():
        p = p / "name_variants.json"
    if not p.exists():
        return EMPTY_VARIANTS
    return compile_variants(json.loads(p.read_text(encoding="utf-8")))


_OBJ_CACHE: dict[int, tuple[Any, Variants]] = {}


def load_variants(source: Any = None) -> Variants:
    """Variants from a Variants, parsed JSON (dict/list, e.g. Dataset.name_variants),
    a path to name_variants.json or its folder, or None. Compiled once per object."""
    if source is None:
        return EMPTY_VARIANTS
    if isinstance(source, Variants):
        return source
    if isinstance(source, (str, Path)):
        return _load_file(str(Path(source).resolve()))
    cached = _OBJ_CACHE.get(id(source))
    if cached is not None and cached[0] is source:
        return cached[1]
    compiled = compile_variants(source)
    if len(_OBJ_CACHE) > 64:
        _OBJ_CACHE.clear()
    _OBJ_CACHE[id(source)] = (source, compiled)  # holding the object keeps its id unique
    return compiled


# ------------------------------------------------------------------ parts


def name_parts(
    name: str | None,
    *,
    company: bool = False,
    legal_forms: Any = frozenset(),
    fold: bool = False,
) -> tuple[str, ...]:
    """Distinct comparable parts of a name, in first-seen order.

    Ignores case, accents, punctuation and anything in brackets. Single letters
    are initials (not parts). Placeholders and digit-only tokens are not parts.
    For companies the legal forms (a set of forms, or a Variants) are not parts.
    ``fold=True`` also maps letters NFKD keeps (l-stroke, o-slash, eszett ...) to
    ASCII; the comparison functions use it. The default keeps the historical
    output, which is how search results are filed (``Pawel`` with a stroke -> ``pawe``).
    """
    if not name:
        return ()
    tokens = _raw_tokens(name, fold)
    if company:
        if isinstance(legal_forms, Variants):
            singles, sequences = legal_forms.company_forms()
        else:
            singles = frozenset(_norm_key(f) for f in legal_forms)
            sequences = ()
        for seq in sequences:
            n = len(seq)
            i, kept = 0, []
            while i < len(tokens):
                if tuple(tokens[i:i + n]) == seq:
                    i += n
                else:
                    kept.append(tokens[i])
                    i += 1
            tokens = kept
        tokens = [t for t in tokens if t not in singles]
    out: list[str] = []
    for token in tokens:
        if len(token) < 2 or _is_placeholder(token):
            continue
        if token not in out:
            out.append(token)
    return tuple(out)


def real_part_count(name: str | None, *, company: bool = False, variants: Any = None) -> int:
    """Number of real name parts (placeholders, initials, digits, legal forms excluded)."""
    return len(name_parts(name, company=company, legal_forms=load_variants(variants), fold=True))


def has_enough_parts(name: str | None, *, company: bool = False, variants: Any = None) -> bool:
    """SOP 8: an individual needs at least two real parts to be confirmed or cleared
    on name; a company needs at least one."""
    return real_part_count(name, company=company, variants=variants) >= (1 if company else 2)


# ------------------------------------------------------------------ part comparison


def levenshtein(a: str, b: str) -> int:
    """Plain Levenshtein distance (insert, delete, substitute; a swap costs 2)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def allowed_edits(a: str, b: str) -> int:
    shorter = min(len(a), len(b))
    if shorter < 4:
        return 0
    return 1 if shorter <= 6 else 2


_EXACT, _VARIANT, _FUZZY = 0, 1, 2


def _relation(a: str, b: str, v: Variants) -> int | None:
    if a == b:
        return _EXACT
    if v.listed_together(a, b):
        return _VARIANT
    limit = allowed_edits(a, b)
    if limit and abs(len(a) - len(b)) <= limit and levenshtein(a, b) <= limit:
        return _FUZZY
    return None


def parts_match(a: str, b: str, variants: Any = None) -> bool:
    """SOP 8: identical, listed together in name_variants.json, or within edit
    distance (1 for 4-6 letters, 2 for longer; <4 letters must be identical;
    the shorter part's length decides). Joined/split parts are handled in
    names_agree()."""
    ka, kb = _norm_key(a or ""), _norm_key(b or "")
    if not ka or not kb:
        return False
    return _relation(ka, kb, load_variants(variants)) is not None


# ------------------------------------------------------------------ name agreement


@dataclass(frozen=True)
class NameMatch:
    agrees: bool
    matched: tuple[tuple[str, str], ...]  # (customer part, hit part) pairs
    hit_name: str  # which hit name / alias agreed (for the reason text)
    fuzzy: bool  # True if any pair matched only by edit distance -> weak evidence
    judgeable: bool = True  # False: cannot confirm or clear on name (see why)
    why: str = ""  # "" | CUSTOMER_FEWER_THAN_TWO_REAL_PARTS | NO_COMPARABLE_HIT_NAME | ...


CANNOT_JUDGE_FEW_PARTS = "CUSTOMER_FEWER_THAN_TWO_REAL_PARTS"
CANNOT_JUDGE_NO_CUSTOMER_PARTS = "CUSTOMER_NO_REAL_PARTS"
CANNOT_JUDGE_NO_HIT_NAME = "NO_COMPARABLE_HIT_NAME"
CANNOT_JUDGE_TOO_COMPLEX = "NAME_TOO_COMPLEX"


def _cannot_judge(why: str) -> NameMatch:
    return NameMatch(agrees=False, matched=(), hit_name="", fuzzy=False, judgeable=False, why=why)


def _units(parts: Sequence[str]) -> list[tuple[tuple[int, ...], str]]:
    units = [((i,), p) for i, p in enumerate(parts)]
    for n in range(2, _MAX_RUN + 1):
        for i in range(len(parts) - n + 1):
            units.append((tuple(range(i, i + n)), "".join(parts[i:i + n])))
    return units


@dataclass
class _Search:
    edges_by_start: dict[int, list[tuple[tuple[int, ...], tuple[int, ...], int]]]
    n_c: int
    n_h: int
    nodes: int = 0
    best_score: tuple | None = None
    best: tuple = ()
    exhausted: bool = False


def _score(chosen: list, n_c: int, n_h: int) -> tuple:
    matches = len(chosen)
    cov_c = sum(len(c) for c, _, _ in chosen)
    cov_h = sum(len(h) for _, h, _ in chosen)
    shortest = min(n_c, n_h)
    full_shorter = (n_c == shortest and cov_c == n_c) or (n_h == shortest and cov_h == n_h)
    agrees = matches >= 2 or (matches >= 1 and full_shorter)
    fuzzy = any(kind == _FUZZY for _, _, kind in chosen)
    exactish = sum(1 for _, _, kind in chosen if kind == _EXACT)
    return (agrees, not fuzzy, matches, cov_c + cov_h, exactish)


def _dfs(s: _Search, i: int, used_c: int, used_h: int, chosen: list) -> None:
    s.nodes += 1
    if s.nodes > _NODE_BUDGET:
        s.exhausted = True
        return
    while i < s.n_c and (used_c >> i) & 1:
        i += 1
    if i >= s.n_c:
        score = _score(chosen, s.n_c, s.n_h)
        if s.best_score is None or score > s.best_score:
            s.best_score, s.best = score, tuple(chosen)
        return
    for c_idx, h_idx, kind in s.edges_by_start.get(i, ()):
        cmask = sum(1 << k for k in c_idx)
        hmask = sum(1 << k for k in h_idx)
        if used_c & cmask or used_h & hmask:
            continue
        chosen.append((c_idx, h_idx, kind))
        _dfs(s, i + 1, used_c | cmask, used_h | hmask, chosen)
        chosen.pop()
        if s.exhausted:
            return
    _dfs(s, i + 1, used_c | (1 << i), used_h, chosen)  # leave part i unmatched


def _match_parts(c_parts: Sequence[str], h_parts: Sequence[str], v: Variants):
    """Best one-to-one assignment. Returns (score, chosen, exhausted)."""
    c_units, h_units = _units(c_parts), _units(h_parts)
    edges: dict[int, list] = {}
    for c_idx, c_key in c_units:
        for h_idx, h_key in h_units:
            if len(c_idx) > 1 and len(h_idx) > 1:
                continue  # run-vs-run is just several single matches
            kind = _relation(c_key, h_key, v)
            if kind is not None:
                edges.setdefault(c_idx[0], []).append((c_idx, h_idx, kind))
    for lst in edges.values():
        lst.sort(key=lambda e: (e[2], len(e[0]) + len(e[1]), e[0], e[1]))
    s = _Search(edges_by_start=edges, n_c=len(c_parts), n_h=len(h_parts))
    _dfs(s, 0, 0, 0, [])
    return s.best_score, s.best, s.exhausted


def names_agree(
    customer_name: str | Sequence[str] | None,
    hit_names: str | Iterable[str] | None,
    variants: Any = None,
    *,
    company: bool = False,
):
    """Return NameMatch for the best agreeing hit name/alias, None on a clean
    non-agreement, or a NameMatch with judgeable=False when the name cannot
    decide either way (individual customer with fewer than two real parts, no
    comparable hit name).

    Names agree when >= 2 parts match, or every part of the shorter name matches.
    ``variants`` is Dataset.name_variants (parsed JSON), a path, or a Variants.
    ``company=True`` drops the legal forms from both sides.
    """
    v = load_variants(variants)
    customers = [customer_name] if isinstance(customer_name, str) or customer_name is None \
        else list(customer_name)
    hits = [hit_names] if isinstance(hit_names, str) else list(hit_names or ())

    c_part_lists = []
    for cname in customers:
        parts = name_parts(cname, company=company, legal_forms=v, fold=True)
        if len(parts) >= (1 if company else 2):
            c_part_lists.append(parts)
    if not c_part_lists:
        any_parts = any(name_parts(c, company=company, legal_forms=v, fold=True) for c in customers)
        if company or not any_parts:
            return _cannot_judge(CANNOT_JUDGE_NO_CUSTOMER_PARTS)
        return _cannot_judge(CANNOT_JUDGE_FEW_PARTS)

    h_named = [(h, name_parts(h, company=company, legal_forms=v, fold=True)) for h in hits if h]
    h_named = [(h, p) for h, p in h_named if p]
    if not h_named:
        return _cannot_judge(CANNOT_JUDGE_NO_HIT_NAME)

    best_key = None
    best_match: NameMatch | None = None
    exhausted_any = False
    for c_parts in c_part_lists:
        for h_name, h_parts in h_named:
            score, chosen, exhausted = _match_parts(c_parts, h_parts, v)
            exhausted_any = exhausted_any or exhausted
            if not score or not score[0]:
                continue
            if best_key is None or score > best_key:
                pairs = tuple(
                    (" ".join(c_parts[k] for k in c_idx), " ".join(h_parts[k] for k in h_idx))
                    for c_idx, h_idx, _ in sorted(chosen)
                )
                best_key = score
                best_match = NameMatch(
                    agrees=True, matched=pairs, hit_name=h_name, fuzzy=not score[1]
                )
    if best_match is not None:
        return best_match
    if exhausted_any:
        return _cannot_judge(CANNOT_JUDGE_TOO_COMPLEX)
    return None
