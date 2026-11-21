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
"""

from __future__ import annotations

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.names import names_agree
from .base import Context

RULE_ID = "P6.name"


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
}


def _check(ctx: Context) -> list[RuleResult]:
    hit_names = tuple(n for n in (ctx.facts.names or ()) if n)
    company = bool(ctx.facts.is_company) or ctx.customer_is_company
    variants = ctx.ds.name_variants
    person = ctx.person
    hit_label = hit_names[0] if hit_names else (ctx.hit.name or "")

    def agree(customer):
        return names_agree(customer, hit_names, variants, company=company)

    notes: list[str] = []

    # P4 + P6: the verified legal name decides when it can be judged.
    v_name, v_source = _verified(person)
    if v_name:
        m = agree(v_name)
        src = f" ({v_source})" if v_source else ""
        if m is None:
            return [
                RuleResult(
                    rule_id=RULE_ID,
                    facet=Facet.NAME,
                    signal=Signal.CONTRADICTION,
                    reason=ReasonCode.NAME_MISMATCH_VERIFIED,
                    summary=f"The customer's verified legal name {v_name}{src} agrees with none "
                    f"of the hit's names or aliases (the hit's name is {hit_label}).",
                    evidence=(v_source,) if v_source else tuple(person.record_ids),
                )
            ]
        if m.agrees:
            return [
                _agreement(
                    f"The customer's verified legal name {v_name}{src} agrees with the hit's "
                    f"name {m.hit_name}.",
                    (v_source,) if v_source else tuple(person.record_ids),
                    m,
                    None,
                )
            ]
        notes.append(
            f"The customer's verified legal name {v_name}{src} "
            f"{_WHY_TEXT.get(m.why, 'cannot be judged')}, so it neither confirms nor clears."
        )

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


def _same_parts(name: str, m, company: bool, variants) -> bool:
    """True if ``name`` alone agrees with the hit name that matched (picks the record to cite)."""
    single = names_agree(name, m.hit_name, variants, company=company)
    return single is not None and single.agrees


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # never raise for messy data: abstain
        return []
