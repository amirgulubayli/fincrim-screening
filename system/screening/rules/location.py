"""P6 Location (contradiction) and P7 country agreement.

SOP P6: every country tied to the customer (residence and nationality on all
their records, the approved identity document, places the notes say they lived)
differs from every country tied to the hit -> LOCATION_MISMATCH. SOP P7: a shared
country is a country agreement (used to confirm companies, and to upgrade a
person's NAME_DOB_MATCH to NAME_DOB_LOCATION_MATCH).

Safety (engine plan R5-R7):
  * a contradiction needs BOTH sets non-empty and NO value on either side that we
    could not read ("unknown") or that could be several countries ("ambiguous"):
    a dropped value can hide a shared country. "International", "Europe", blanks
    and bracket tags are not countries and are simply ignored.
  * no resolved customer -> never a contradiction; a weak agreement from the
    screened country is allowed (COUNTRY_FROM_SCREENED_ONLY).
  * the compiler turns a location-only contradiction into REVIEW when name and
    DOB agree; that is not repeated here.

The customer's country claims come from Person.countries (CountryClaim: raw,
source_id, kind) when the resolver provides them; otherwise they are rebuilt
here from the records, the approved KYC checks and the notes.
"""

from __future__ import annotations

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.countries import classify_country, countries_in_text
from .base import Context

RULE_ID = "P6.location"

_BLOCKING = ("unknown", "ambiguous")
WEAK_SCREENED_ONLY = "COUNTRY_FROM_SCREENED_ONLY"
WEAK_NOTE_TEXT = "COUNTRY_FROM_NOTE_TEXT"


def _neutral(summary: str) -> list[RuleResult]:
    return [RuleResult(rule_id=RULE_ID, facet=Facet.LOCATION, signal=Signal.NEUTRAL, summary=summary)]


def _join(names: list[str]) -> str:
    return ", ".join(names)


# ------------------------------------------------------------------ customer side


def _customer_claims(person: object) -> list[tuple[str, str, str]]:
    """(raw value, source id, kind) for every country claim of the person."""
    claims = getattr(person, "countries", None)
    if claims is not None:  # upgraded resolver: authoritative list of claims
        out = []
        for c in claims:
            raw = getattr(c, "raw", None)
            if raw is not None:
                out.append((str(raw), str(getattr(c, "source_id", "") or ""), str(getattr(c, "kind", ""))))
        return out
    out = []
    for rec in getattr(person, "records", ()) or ():
        if rec.residence_country:
            out.append((rec.residence_country, rec.record_id, "residence"))
        for nat in rec.nationality:
            out.append((nat, rec.record_id, "nationality"))
    for chk in getattr(person, "kyc", ()) or ():
        if chk.document_country:
            out.append((chk.document_country, chk.check_id, "document"))
    # Old resolver: no note parser. Every country a note names is taken as tied
    # to the customer - over-inclusion can only block a clear, never cause one.
    for note in getattr(person, "notes", ()) or ():
        for canon in countries_in_text(note.text):
            out.append((canon, note.note_id, "note_text"))
    return out


def _classify_customer(
    claims: list[tuple[str, str, str]],
) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """({canonical: [(source_id, kind)]}, unreadable raw values)."""
    found: dict[str, list[tuple[str, str]]] = {}
    unreadable: list[str] = []
    for raw, source_id, kind in claims:
        canon, status = classify_country(raw)
        if status == "country" and canon:
            found.setdefault(canon, []).append((source_id, kind))
        elif status in _BLOCKING:
            unreadable.append(raw)
    return found, unreadable


# ----------------------------------------------------------------------- hit side


def _hit_values(ctx: Context) -> list[str]:
    hit = ctx.hit
    values = [*hit.countries, *hit.nationality, *hit.citizenship, *hit.addresses]
    if hit.place_of_birth:
        values.append(hit.place_of_birth)
    return [v for v in values if v and str(v).strip()]


def _classify_hit(ctx: Context) -> tuple[set[str], list[str]]:
    """(canonical countries tied to the hit, unreadable values)."""
    found: set[str] = set(ctx.facts.countries or ())
    unreadable: list[str] = []
    # facts.countries_raw: the last part of each country-bearing value
    for raw in ctx.facts.countries_raw or ():
        canon, status = classify_country(raw)
        if status == "country" and canon:
            found.add(canon)
        elif status in _BLOCKING:
            # the full value may still read ("Korea, Republic of" -> last part "Republic of")
            if not any(raw in v and classify_country(v)[1] == "country" for v in _hit_values(ctx)):
                unreadable.append(raw)
    # the raw hit fields themselves (a value facts could not split still counts)
    for value in _hit_values(ctx):
        canon, status = classify_country(value)
        if status == "country" and canon:
            found.add(canon)
    return found, unreadable


# ---------------------------------------------------------------------- the check


def _no_person(ctx: Context, hit_set: set[str]) -> list[RuleResult]:
    canon, status = classify_country(ctx.alert.country)
    if status == "country" and canon and canon in hit_set:
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.LOCATION,
                signal=Signal.AGREEMENT,
                summary=f"The screened country {canon} is also tied to the hit "
                "(no customer record was found to confirm it).",
                evidence=(),
                weak=True,
                weak_why=WEAK_SCREENED_ONLY,
            )
        ]
    return _neutral("No customer record was found, so countries cannot contradict the hit.")


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # messy data never raises out of a rule: abstain
        return []


def _check(ctx: Context) -> list[RuleResult]:
    hit_set, hit_unreadable = _classify_hit(ctx)
    if ctx.person is None:
        return _no_person(ctx, hit_set)

    cust, cust_unreadable = _classify_customer(_customer_claims(ctx.person))
    cust_set = set(cust)
    record_ids = tuple(sorted(ctx.person.record_ids))

    shared = sorted(cust_set & hit_set)
    if shared:
        sources = sorted({sid for c in shared for sid, _ in cust[c] if sid})
        only_note_text = all(kind == "note_text" for c in shared for _, kind in cust[c])
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.LOCATION,
                signal=Signal.AGREEMENT,
                summary=f"Both the customer and the hit are linked to {_join(shared)}.",
                evidence=tuple(sources) or record_ids,
                weak=only_note_text,
                weak_why=WEAK_NOTE_TEXT if only_note_text else None,
            )
        ]

    if not cust_set or not hit_set:
        side = "customer" if not cust_set else "hit"
        return _neutral(f"No country is known for the {side}, so location cannot settle it.")
    if cust_unreadable or hit_unreadable:
        unread = sorted(set(cust_unreadable) | set(hit_unreadable))
        return _neutral(
            f"Some country values could not be read ({_join(unread)}); they might be shared, "
            "so location cannot clear the hit."
        )

    # Disjoint, complete, readable on both sides -> contradiction.
    non_record = sorted({sid for c in cust_set for sid, _ in cust[c] if sid and sid not in record_ids})
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.LOCATION,
            signal=Signal.CONTRADICTION,
            reason=ReasonCode.LOCATION_MISMATCH,
            summary=f"The customer is linked only to {_join(sorted(cust_set))} "
            f"but the hit is linked only to {_join(sorted(hit_set))}.",
            evidence=(*record_ids, *non_record),
        )
    ]
