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
  * SOP P6: a country difference does not clear a hit whose name and DOB agree.
    When the P6.name and P6.dob rules both agree, the contradiction is still
    emitted so the compiler turns it into REVIEW with its standard explanation.
    As a safety net for when those rules abstain, the contradiction is withheld
    whenever a customer DOB could be one of the hit's (every part both give is
    equal, R6), or the name agrees while a DOB on file could not be read.

Head of Financial Crime policy calls (contrib/aml-bot README, always the more
cautious reading):
  * countries that were once one state (former Yugoslavia incl. Serbia and
    Montenegro and Kosovo, the Soviet Union, Czechoslovakia, Sudan/South Sudan,
    Ethiopia/Eritrea): listing data from before the split may name either
    country for the same person, so the difference is not a LOCATION_MISMATCH.
    Neighbours that were never one state still contradict.
  * an individual hit whose ONLY country links are business addresses
    ("... [Business]") does not support a location clearance: a business address
    is where someone works or registered a company, not residence or nationality.
  * every country named in any of the customer's notes counts towards the
    customer's countries (over-inclusion can only block a clear, never cause one).

The customer's country claims come from Person.countries (CountryClaim: raw,
source_id, kind) when the resolver provides them; otherwise they are rebuilt
here from the records, the approved KYC checks and the notes.
"""

from __future__ import annotations

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.countries import classify_country, countries_in_text
from ..normalize.dates import dates_agree, parse_date
from . import dob as dob_rule
from . import name as name_rule
from .base import Context, is_company

RULE_ID = "P6.location"

_BLOCKING = ("unknown", "ambiguous")
WEAK_SCREENED_ONLY = "COUNTRY_FROM_SCREENED_ONLY"
WEAK_NOTE_TEXT = "COUNTRY_FROM_NOTE_TEXT"

# Countries that were one state; canonical names as normalize.countries spells them.
FORMER_STATES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "Yugoslavia (later Serbia and Montenegro)",
        frozenset({
            "Bosnia and Herzegovina", "Croatia", "Kosovo", "Montenegro", "North Macedonia",
            "Serbia", "Slovenia",
        }),
    ),
    (
        "the Soviet Union",
        frozenset({
            "Armenia", "Azerbaijan", "Belarus", "Estonia", "Georgia", "Kazakhstan", "Kyrgyzstan",
            "Latvia", "Lithuania", "Moldova", "Russia", "Tajikistan", "Turkmenistan", "Ukraine",
            "Uzbekistan",
        }),
    ),
    ("Czechoslovakia", frozenset({"Czech Republic", "Slovakia"})),
    ("Sudan (before South Sudan's independence)", frozenset({"South Sudan", "Sudan"})),
    ("Ethiopia (before Eritrea's independence)", frozenset({"Eritrea", "Ethiopia"})),
)


def _neutral(summary: str) -> list[RuleResult]:
    return [RuleResult(rule_id=RULE_ID, facet=Facet.LOCATION, signal=Signal.NEUTRAL, summary=summary)]


def _join(names: list[str]) -> str:
    return ", ".join(names)


# ------------------------------------------------------------------ customer side


def _note_mentions(person: object) -> list[tuple[str, str, str]]:
    """Every country named anywhere in the customer's notes (HoFC call 9)."""
    out = []
    for note in sorted(getattr(person, "notes", ()) or (), key=lambda n: str(n.note_id)):
        for canon in countries_in_text(note.text):
            out.append((canon, str(note.note_id), "note_text"))
    return out


def _customer_claims(person: object) -> list[tuple[str, str, str]]:
    """(raw value, source id, kind) for every country claim of the person."""
    claims = getattr(person, "countries", None)
    if claims is not None:  # upgraded resolver: authoritative list of claims
        out = []
        for c in claims:
            raw = getattr(c, "raw", None)
            if raw is not None:
                out.append((str(raw), str(getattr(c, "source_id", "") or ""), str(getattr(c, "kind", ""))))
        # the resolver only reads residence wording; every other mention counts too
        seen = {(source_id, classify_country(raw)[0]) for raw, source_id, _ in out}
        out.extend(m for m in _note_mentions(person) if (m[1], m[0]) not in seen)
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
    out.extend(_note_mentions(person))
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


def _value_country(value: str) -> str | None:
    canon, status = classify_country(value)
    if status == "country" and canon:
        return canon
    last = str(value).replace("[", ",[").split(",")
    parts = [p.strip() for p in last if p.strip() and not p.strip().startswith("[")]
    if parts:
        canon, status = classify_country(parts[-1])
        if status == "country" and canon:
            return canon
    return None


def _business_only(ctx: Context) -> bool:
    """Every country link of the hit is an address tagged [Business] (HoFC call 6)."""
    hit = ctx.hit
    others = [*hit.countries, *hit.nationality, *hit.citizenship]
    if hit.place_of_birth:
        others.append(hit.place_of_birth)
    if any(v and _value_country(str(v)) for v in others):
        return False
    linked = [str(a) for a in hit.addresses if a and _value_country(str(a))]
    return bool(linked) and all("[business]" in a.lower() for a in linked)


def _involves_company(ctx: Context) -> bool:
    records = getattr(ctx.person, "records", None) or ()
    etype = (records[0].entity_type if records else None) or ctx.alert.entity_type
    return is_company(etype) or bool(ctx.facts.is_company)


def _former_state(cust_set: set[str], hit_set: set[str]) -> tuple[str, list[str], list[str]] | None:
    for state, members in FORMER_STATES:
        cust_in, hit_in = sorted(cust_set & members), sorted(hit_set & members)
        if cust_in and hit_in:
            return state, cust_in, hit_in
    return None


# ------------------------------------------------------------ R6 safety net


def _customer_dob_raws(ctx: Context) -> list[str]:
    """Every DOB the bank holds for the person (verified checks and records).

    The screened DOB on the alert is a copy of what is on file, often shortened to
    a year; it is used only when the person has no DOB of their own on file.
    """
    person = ctx.person
    raws = [getattr(person, "verified_dob", None)]
    raws.extend(getattr(i, "dob_raw", None) for i in getattr(person, "identities", ()) or ())
    raws.extend(r.dob for r in getattr(person, "records", ()) or ())
    raws.extend(k.verified_dob for k in getattr(person, "kyc", ()) or ())
    out = [str(r).strip() for r in raws if r and str(r).strip()]
    if ctx.alert.dob and not any(parse_date(r) for r in out):
        out.append(str(ctx.alert.dob).strip())
    return out


def _dobs_may_agree(ctx: Context) -> list[str]:
    """Customer DOBs that could be one of the hit's birth dates (over-inclusive on purpose).

    SOP P6: a country difference does not clear a hit whose name and DOB agree.
    The compiler enforces that from the P6.dob agreement, but only when that rule
    speaks; this keeps a location clear from slipping through while it does not.
    Any customer DOB (verified, record, screened) that agrees in every part both
    give with one hit birth date (an age gives years only) blocks the location
    contradiction: abstaining only ever turns a clear into REVIEW.
    """
    hit_dates = []
    hit_years: set[int] = set()
    for cand in ctx.facts.dob_candidates or ():
        if cand.date is not None:
            hit_dates.append(cand.date)
        else:
            hit_years.update(y for y in (cand.years or ()) if isinstance(y, int))
    for raw in ctx.facts.dobs_raw or ():
        parsed = parse_date(raw)
        if parsed is not None:
            hit_dates.append(parsed)
    if not hit_dates and not hit_years:
        return []
    out: set[str] = set()
    for raw in _customer_dob_raws(ctx):
        cust = parse_date(raw)
        if cust is None:
            continue
        if cust.year in hit_years or any(dates_agree(cust, h) for h in hit_dates):
            out.add(str(cust))
    return sorted(out)


def _unreadable_dobs(ctx: Context) -> list[str]:
    """DOB values on file (either side) that could not be read."""
    bad = [r for r in _customer_dob_raws(ctx) if parse_date(r) is None]
    bad.extend(str(v) for v in ctx.hit.dobs if v and str(v).strip() and parse_date(v) is None)
    bad.extend(ctx.facts.notes_dob_unparsed or ())
    return sorted(set(bad))


def _agrees(rule, ctx: Context) -> bool:
    return any(r.signal is Signal.AGREEMENT for r in rule.check(ctx))


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

    cust_names, hit_names = _join(sorted(cust_set)), _join(sorted(hit_set))
    non_record = sorted({sid for c in cust_set for sid, _ in cust[c] if sid and sid not in record_ids})
    contradiction = RuleResult(
        rule_id=RULE_ID,
        facet=Facet.LOCATION,
        signal=Signal.CONTRADICTION,
        reason=ReasonCode.LOCATION_MISMATCH,
        summary=f"The customer is linked only to {cust_names} but the hit is linked only to {hit_names}.",
        evidence=(*record_ids, *non_record),
    )

    # Name and DOB agree: the compiler turns this location-only contradiction into REVIEW.
    name_agrees = _agrees(name_rule, ctx)
    if name_agrees and _agrees(dob_rule, ctx):
        return [contradiction]

    former = _former_state(cust_set, hit_set)
    if former:
        state, cust_in, hit_in = former
        return _neutral(
            f"The customer is linked to {_join(cust_in)} and the hit to {_join(hit_in)}, which were once "
            f"one state, {state}; listing data from before the split may name either country for the "
            "same person, so this is not a location mismatch."
        )
    if _business_only(ctx) and not _involves_company(ctx):
        return _neutral(
            f"The hit's only country link is a business address in {hit_names}; a business address "
            "shows where a person works or registered a company, not where they live or their "
            "nationality, so it does not support a location clearance."
        )

    may_agree = _dobs_may_agree(ctx)
    if may_agree:
        return _neutral(
            f"The countries differ ({cust_names} vs {hit_names}), but the customer's date of birth "
            f"{_join(may_agree)} may agree with the hit's, and a country difference does not clear a "
            "hit whose date of birth may agree."
        )
    unreadable_dobs = _unreadable_dobs(ctx) if name_agrees else []
    if unreadable_dobs:
        return _neutral(
            f"The countries differ ({cust_names} vs {hit_names}) and the names agree, but a date of "
            f"birth on file could not be read ({_join(unreadable_dobs[:3])}), so a name and date of "
            "birth match cannot be ruled out."
        )

    # Disjoint, complete, readable on both sides -> contradiction.
    return [contradiction]
