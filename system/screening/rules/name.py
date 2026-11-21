"""P6 Name + P8 name agreement.

CONTRADICTION (NAME_MISMATCH_VERIFIED) only when the customer has a verified legal
name from their most recent *approved* identity check (P4: KYC provider or an
onboarding-team note) and that name agrees with none of the hit's names or aliases
(P8), even if the name on their record does. Never from a record, preferred, login
or screened name, and never when the name cannot be judged (placeholders, fewer
than two real parts, no comparable hit name): that is NEUTRAL.

AGREEMENT (P7 needs it to confirm) when the verified legal name agrees; with no
verified name to judge on, when a record full or preferred name agrees; with no
customer resolved at all, when the screened name agrees (weak). An agreement that
rests on an edit-distance-only part match is weak (NAME_FUZZY_MATCH).

Head of Financial Crime policy calls (contrib/aml-bot/engine/README.md), applied
before a NAME_MISMATCH_VERIFIED clearance:

* Every approved verified name counts: an older approved check whose name agrees
  with the hit, or that cannot be judged, blocks the clearance.
* Middle rule: when the verified name shares any part with a hit name or alias,
  clear only when the single shared part is the surname on both sides and both
  sides have full given names that clearly differ (no variant, initial,
  prefix/diminutive, joined form or abbreviation link). Otherwise NEUTRAL.
* A shared surname that is commonly a father's given name (Umar, Ahmed, Ibrahim
  ...; expanded with name_variants.json groups) never clears: name order is
  unreliable in those naming traditions.
"""

from __future__ import annotations

import re

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.names import (
    NameMatch,
    allowed_edits,
    levenshtein,
    load_variants,
    name_parts,
    names_agree,
    parts_match,
    strip_accents,
)
from .base import Context

RULE_ID = "P6.name"

# Surnames that are commonly a father's (or grandfather's) given name in Arabic, Hausa,
# Islamic and West African naming traditions: part order varies between documents, so a
# surname-only overlap cannot clear (HoFC policy call 4).
PATRONYMIC_SURNAMES = frozenset(
    {
        "abdul", "abdel", "abdallah", "abdulla", "abdullah", "abdullahi", "abubakar", "abubakr",
        "aboubakar", "adamu", "ahmad", "ahmed", "ahmet", "ali", "aliyu", "aly", "alhasan",
        "alhassan", "amin", "aminu", "audu", "bashir", "bello", "danjuma", "dauda", "ebrahim",
        "faruk", "farouk", "garba", "hamid", "haruna", "hasan", "hassan", "hossain", "hosein",
        "husain", "hussain", "hussein", "ibraheem", "ibrahim", "idris", "isa", "ismail", "issa",
        "jamal", "jibril", "kabir", "kamal", "karim", "khalid", "khalil", "lawal", "mahmoud",
        "mahmud", "malik", "mohamad", "mohamed", "mohammad", "mohammed", "mohd", "moussa",
        "muhamad", "muhammad", "muhammed", "musa", "mustafa", "mustapha", "nasir", "nuhu",
        "omar", "omer", "osman", "othman", "rahim", "rahman", "rashid", "saeed", "said",
        "salah", "saleh", "salim", "salisu", "sani", "shehu", "sulaiman", "sulayman",
        "suleiman", "suleman", "tijani", "umar", "usman", "uthman", "yahaya", "yahya",
        "yakubu", "yousef", "yousuf", "youssef", "yusuf", "zakari", "zakariya",
    }
)
_BRACKETED = re.compile(r"[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]")


def _verified(person) -> tuple[str | None, str | None]:
    """(verified legal name, source id) from the most recent approved check."""
    if person is None:
        return None, None
    if hasattr(person, "verified"):  # resolver with P4 verified identity
        name = getattr(person, "verified_name", None)
        source = getattr(person, "verified_source_id", None)
        return (name or None), (source or None)
    latest = getattr(person, "latest_kyc", None)  # approved KYC checks only, newest first
    if latest is None or not latest.verified_name:
        return None, None
    return latest.verified_name, latest.check_id


def _record_names(person) -> tuple[list[str], dict[str, str]]:
    """Record full / preferred names (distinct, record-id order) and the record each came from."""
    names: list[str] = []
    origin: dict[str, str] = {}
    for rec in sorted(getattr(person, "records", ()) or (), key=lambda r: r.record_id):
        for value in (rec.full_name, rec.preferred_name):
            if value and value.strip() and value not in origin:
                names.append(value)
                origin[value] = rec.record_id
    return names, origin


def _agreement(summary: str, evidence: tuple[str, ...], match, weak_why: str | None) -> RuleResult:
    if weak_why is None and match.fuzzy:
        weak_why = "NAME_FUZZY_MATCH"
    return RuleResult(
        rule_id=RULE_ID,
        facet=Facet.NAME,
        signal=Signal.AGREEMENT,
        summary=summary,
        evidence=evidence,
        weak=weak_why is not None,
        weak_why=weak_why,
    )


def _neutral(summary: str) -> RuleResult:
    return RuleResult(rule_id=RULE_ID, facet=Facet.NAME, signal=Signal.NEUTRAL, summary=summary)


_WHY_TEXT = {
    "CUSTOMER_FEWER_THAN_TWO_REAL_PARTS": "has fewer than two real name parts",
    "CUSTOMER_NO_REAL_PARTS": "has no real name parts",
    "NO_COMPARABLE_HIT_NAME": "cannot be compared: the hit has no comparable name",
    "NAME_TOO_COMPLEX": "is too complex to compare reliably",
    "SINGLE_JOINED_PART": (
        "shares only one name part with the hit (several parts written together on one side), "
        "too weak to agree"
    ),
}
SINGLE_JOINED_PART = "SINGLE_JOINED_PART"


def _counted(m, customer: str, company: bool, v) -> bool:
    """Policy name rule counts matched parts: >= 2, or every part of a one-part shorter name.
    A joined form ('Blue Grass' ~ 'Bluegrass') is ONE matched part, so it alone cannot
    make two-part names agree (HoFC call 1: a single shared part is too weak)."""
    if len(m.matched) >= 2:
        return True
    shorter = min(len(_parts(customer, company, v)), len(_parts(m.hit_name, company, v)))
    return shorter <= 1


def _agree_counted(customer, hit_names, variants, company):
    """names_agree(), with each joined form counted as one matched part (see _counted)."""
    m = names_agree(customer, hit_names, variants, company=company)
    if m is None or not m.agrees:
        return m
    v = load_variants(variants)
    customers = [customer] if isinstance(customer, str) else list(customer)
    if len(m.matched) >= 2:
        return m
    found = []
    for c in customers:
        for h in hit_names:
            mh = names_agree(c, (h,), variants, company=company)
            if mh is not None and mh.agrees and _counted(mh, c, company, v):
                if mh.hit_name == m.hit_name and mh.matched == m.matched:
                    return m  # the best match itself counts
                found.append(mh)
    if found:
        return found[0]
    return NameMatch(agrees=False, matched=m.matched, hit_name=m.hit_name, fuzzy=m.fuzzy,
                     judgeable=False, why=SINGLE_JOINED_PART)


def _check(ctx: Context) -> list[RuleResult]:
    hit_names = tuple(n for n in (ctx.facts.names or ()) if n)
    company = bool(ctx.facts.is_company) or ctx.customer_is_company
    variants = ctx.ds.name_variants
    person = ctx.person
    hit_label = hit_names[0] if hit_names else (ctx.hit.name or "")

    def agree(customer):
        return _agree_counted(customer, hit_names, variants, company)

    notes: list[str] = []

    # P4 + P6: the verified legal name decides when it can be judged.
    v_name, v_source = _verified(person)
    if v_name:
        m = agree(v_name)
        src = f" ({v_source})" if v_source else ""
        if m is None:
            blocked = _mismatch_blocker(person, v_name, v_source, hit_names, company, variants)
            if blocked is None:
                return [_mismatch(person, v_name, v_source, hit_names, hit_label, company, variants)]
            # The verified name still outranks the record names: no agreement from them.
            return [_neutral(blocked)]
        elif m.agrees:
            return [
                _agreement(
                    f"The customer's verified legal name {v_name}{src} agrees with the hit's "
                    f"name {m.hit_name}.",
                    (v_source,) if v_source else tuple(person.record_ids),
                    m,
                    None,
                )
            ]
        else:
            text = (
                f"The customer's verified legal name {v_name}{src} "
                f"{_WHY_TEXT.get(m.why, 'cannot be judged')}, so it neither confirms nor clears."
            )
            if m.why == SINGLE_JOINED_PART:  # judged, just too weak: record names cannot outrank it
                return [_neutral(text)]
            notes.append(text)

    # No judgeable verified name: record names may agree, never contradict.
    if person is not None:
        names, origin = _record_names(person)
        if names:
            m = agree(names)
            if m is not None and m.agrees:
                rec_id = next(
                    (origin[n] for n in names if _same_parts(n, m, company, variants)),
                    min(origin.values()),
                )
                return [
                    _agreement(
                        f"The customer's name agrees with the hit's name {m.hit_name}.",
                        (rec_id,),
                        m,
                        None,
                    )
                ]
            if m is None:
                notes.append(
                    "The customer's record name agrees with none of the hit's names, but only a "
                    "verified legal name can clear on name."
                )
            else:
                notes.append(
                    f"The customer's record name {_WHY_TEXT.get(m.why, 'cannot be judged')}, "
                    "so it neither confirms nor clears."
                )
        return [_neutral(" ".join(notes) or "The customer has no name to compare with the hit.")]

    # No customer resolved: the screened name can only give a weak agreement.
    screened = ctx.alert.customer_name
    if screened and screened.strip():
        m = agree(screened)
        if m is not None and m.agrees:
            return [
                _agreement(
                    f"The screened name {screened} agrees with the hit's name {m.hit_name}.",
                    (),
                    m,
                    "NAME_FROM_SCREENED_ONLY",
                )
            ]
        if m is not None:
            return [
                _neutral(
                    f"The screened name {screened} {_WHY_TEXT.get(m.why, 'cannot be judged')}, "
                    "so it neither confirms nor clears."
                )
            ]
        return [
            _neutral(
                "The screened name agrees with none of the hit's names; with no "
                "verified legal name this does not clear."
            )
        ]
    return [_neutral("No customer name to compare with the hit.")]


# --------------------------------------------------------- verified-name mismatch policy


def _identity_names(person, v_name: str, v_source: str | None) -> list[tuple[str, str | None]]:
    """Every approved verified name (latest first, distinct)."""
    out = [(v_name, v_source)]
    for ident in getattr(person, "identities", ()) or ():
        name = getattr(ident, "name", None)
        if name and name.strip() and all(name != n for n, _ in out):
            out.append((name, getattr(ident, "source_id", None)))
    return out


def _parts(name: str, company: bool, v) -> tuple[str, ...]:
    return name_parts(name, company=company, legal_forms=v, fold=True)


def _has_initials(name: str) -> bool:
    """A single-letter token anywhere (outside brackets): 'Zachary M', 'L.P.'."""
    text = strip_accents(name).lower()
    prev = None
    while prev != text:
        prev, text = text, _BRACKETED.sub(" ", text)
    text = text.replace("'", "").replace("’", "")
    return any(len(t) == 1 and t.isalpha() for t in re.split(r"[^0-9a-z]+", text))


def _shared(c_parts, h_parts, v) -> tuple[list[tuple[str, str]], bool]:
    """(customer part, hit part) pairs that match, and whether a joined form matched."""
    pairs = [(c, h) for c in c_parts for h in h_parts if parts_match(c, h, v)]
    joined = any(
        parts_match(a + b, other, v)
        for side, others in ((c_parts, h_parts), (h_parts, c_parts))
        for a, b in zip(side, side[1:], strict=False)
        for other in others
    )
    return pairs, joined


def _hit_surnames(hit_name: str, company: bool, v) -> tuple[str, ...]:
    """'Surname, Given' -> the parts before the comma; otherwise the last part."""
    if "," in hit_name:
        return _parts(hit_name.split(",")[0], company, v)
    return _parts(hit_name, company, v)[-1:]


def _group_members(v) -> dict[int, tuple[str, ...]]:
    members: dict[int, list[str]] = {}
    for key in sorted(v.groups):
        for gid in sorted(v.groups[key]):
            members.setdefault(gid, []).append(key)
    return {g: tuple(ms) for g, ms in members.items()}


def _close(a: str, b: str) -> bool:
    limit = allowed_edits(a, b)
    return bool(limit) and abs(len(a) - len(b)) <= limit and levenshtein(a, b) <= limit


def _variant_link(a: str, b: str, v, members) -> bool:
    """Wider than a part match (used only to BLOCK a clearance): a misspelling of one
    variant of a name still links to the other variants of its group."""
    if parts_match(a, b, v):
        return True
    for x, y in ((a, b), (b, a)):
        for gid in sorted(v.groups.get(y, ())):
            if any(_close(x, m) for m in members.get(gid, ())):
                return True
    return False


def _is_patronymic(part: str, v) -> bool:
    if part in PATRONYMIC_SURNAMES:
        return True
    return len(part) >= 4 and any(
        len(p) >= 4 and v.listed_together(part, p) for p in sorted(PATRONYMIC_SURNAMES)
    )


def _middle_rule(c_name: str, h_name: str, shared: str, company: bool, v):
    """HoFC middle rule for one shared part -> (clearly differ, why not, customer given, hit given)."""
    if _has_initials(c_name) or _has_initials(h_name):
        return False, "one of the names contains initials", (), ()
    c_parts = _parts(c_name, company, v)
    if not c_parts or not parts_match(c_parts[-1], shared, v):
        return False, "the shared part is not the customer's surname (the name may be reordered)", (), ()
    h_surnames = _hit_surnames(h_name, company, v)
    if not any(parts_match(x, shared, v) for x in h_surnames):
        return False, "the shared part is not the listed name's surname (the name may be reordered)", (), ()
    if not company and any(_is_patronymic(x, v) for x in (shared, c_parts[-1], *h_surnames)):
        why = (f"the shared surname {shared.title()} is commonly a father's given name, so the "
               "order of name parts is unreliable")
        return False, why, (), ()
    c_given = tuple(p for p in c_parts if not parts_match(p, shared, v))
    h_given = tuple(
        p for p in _parts(h_name, company, v) if not parts_match(p, shared, v) and p not in h_surnames
    )
    if not c_given or not h_given:
        return False, "one side has no given name to compare", c_given, h_given
    if not any(len(g) >= 3 for g in c_given) or not any(len(g) >= 3 for g in h_given):
        return False, "the given names are abbreviations only", c_given, h_given
    members = _group_members(v)
    c_forms = list(c_given) + [a + b for a, b in zip(c_given, c_given[1:], strict=False)]
    h_forms = list(h_given) + [a + b for a, b in zip(h_given, h_given[1:], strict=False)]
    for a in c_forms:
        for b in h_forms:
            if (
                _variant_link(a, b, v, members)
                or (min(len(a), len(b)) <= 2 and a[0] == b[0])
                or a.startswith(b)
                or b.startswith(a)
            ):
                why = f"the given names {a.title()} and {b.title()} could be the same name"
                return False, why, c_given, h_given
    return True, "", c_given, h_given


def _overlaps(person, v_name, v_source, hit_names, company, v):
    """[(verified name, source, hit name, matching part pairs, joined)] sharing any part."""
    out = []
    for c_name, c_src in _identity_names(person, v_name, v_source):
        c_parts = _parts(c_name, company, v)
        for h_name in hit_names:
            pairs, joined = _shared(c_parts, _parts(h_name, company, v), v)
            if pairs or joined:
                out.append((c_name, c_src, h_name, pairs, joined))
    return out


def _mismatch_blocker(person, v_name, v_source, hit_names, company, variants) -> str | None:
    """Why the verified-name mismatch must not clear (a NEUTRAL sentence), or None."""
    for c_name, c_src in _identity_names(person, v_name, v_source)[1:]:
        m = names_agree(c_name, hit_names, variants, company=company)
        src = f" ({c_src})" if c_src else ""
        if m is not None and m.agrees:
            return (
                f"The customer's latest verified legal name {v_name} agrees with none of the hit's "
                f"names, but an earlier approved check's name {c_name}{src} agrees with "
                f"{m.hit_name}, so the name does not clear."
            )
        if m is not None:
            return (
                f"An earlier approved check's name {c_name}{src} "
                f"{_WHY_TEXT.get(m.why, 'cannot be judged')}, so the name does not clear."
            )
    v = load_variants(variants)
    for c_name, c_src, h_name, pairs, joined in _overlaps(person, v_name, v_source, hit_names, company, v):
        src = f" ({c_src})" if c_src else ""
        c_shared = sorted({c for c, _ in pairs})
        h_shared = sorted({h for _, h in pairs})
        if joined or len(c_shared) != 1 or len(h_shared) != 1:
            return (
                f"The customer's verified legal name {c_name}{src} shares name parts with the hit's "
                f"name {h_name} in a way that cannot be ruled out, so the name does not clear."
            )
        ok, why, _cg, _hg = _middle_rule(c_name, h_name, c_shared[0], company, v)
        if not ok:
            return (
                f"The customer's verified legal name {c_name}{src} shares only one name part, "
                f"'{c_shared[0].title()}', with the hit's name {h_name}: too weak to agree, and "
                f"{why}, so it does not clear either."
            )
    return None


def _mismatch(person, v_name, v_source, hit_names, hit_label, company, variants) -> RuleResult:
    evidence = (v_source,) if v_source else tuple(person.record_ids)
    v = load_variants(variants)
    overlaps = _overlaps(person, v_name, v_source, hit_names, company, v)
    if overlaps:
        c_name, c_src, h_name, pairs, _joined = overlaps[0]
        shared = pairs[0][0]
        _ok, _why, c_given, h_given = _middle_rule(c_name, h_name, shared, company, v)
        evidence = tuple(dict.fromkeys(o[1] for o in overlaps if o[1])) or evidence
        c_src_text = f" ({c_src})" if c_src else ""
        summary = (
            f"The customer's verified legal name {c_name}{c_src_text} shares only the surname "
            f"'{shared.title()}' with the hit's name {h_name}; the given names clearly differ "
            f"({' '.join(g.title() for g in c_given)} versus {' '.join(g.title() for g in h_given)}), "
            "so it agrees with none of the hit's names or aliases."
        )
    else:
        src = f" ({v_source})" if v_source else ""
        summary = (
            f"The customer's verified legal name {v_name}{src} agrees with none "
            f"of the hit's names or aliases (the hit's name is {hit_label})."
        )
    return RuleResult(
        rule_id=RULE_ID,
        facet=Facet.NAME,
        signal=Signal.CONTRADICTION,
        reason=ReasonCode.NAME_MISMATCH_VERIFIED,
        summary=summary,
        evidence=evidence,
    )


def _same_parts(name: str, m, company: bool, variants) -> bool:
    """True if ``name`` alone agrees with the hit name that matched (picks the record to cite)."""
    single = names_agree(name, m.hit_name, variants, company=company)
    return single is not None and single.agrees


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # never raise for messy data: abstain
        return []
