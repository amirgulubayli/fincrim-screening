"""P6 DOB contradiction and P7 DOB agreement.

Customer side (SOP P4, "verified beats declared"): the date of birth of the most
recent approved identity check (KYC-... or NOTE-...), else the date of birth on
the person's customer records. For a company that date is its incorporation date.

Hit side (SOP P5, gathered in facts.py): every DobCandidate. A candidate has one
or more possible years (an age at a date gives two) and, when written, an exact
partial date. Companies: only the `dobs` field counts (engine plan R14).

* CONTRADICTION (DOB_MISMATCH) only when the customer DOB is known, the hit gives
  at least one birth date, EVERY one is contradicted (years more than 1 apart, or
  the same year with differing month/day), no hit DOB was left unread (an unparsed
  `dobs` value or profile-note sentence), and every customer DOB agrees on that.
  Head of Financial Crime policy: "every customer DOB" means EVERY date of birth
  the bank holds for the person - the verified one, every other approved identity
  check and every customer record - and a DOB that is present but unreadable
  anywhere on the person blocks the clearance.
* AGREEMENT when one hit birth date agrees (P7): the customer's year is one of its
  possible years and every part both give is equal. A partial DOB (year only, or
  year + month) agrees too: name + partial DOB confirms (HoFC policy call 1).
* NEUTRAL otherwise, with a sentence saying what was compared or missing.

Ages inside news-report paragraphs ("According to <source>; <date>: ...") are
ignored (HoFC policy call 8): the subject of a news age is unreliable.

Guards from the engine plan: R1 (slash dates are day-first, unparseable values
abstain), R2 (unknown parts stay unknown), R3 (an age gives two years, a clear
needs the customer year more than 1 outside both), R4 (search-result DOBs are used
only when the hit gives no other, and are weak -> BORDERLINE), R14 (company DOB is
the incorporation date, only from `dobs`).

Pure, deterministic, never raises (any error -> []).
"""

from __future__ import annotations

import re

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..facts import WEAK_SEARCH_RESULT, first_paragraph
from ..normalize.dates import PartialDate, dates_agree, dates_contradict, parse_date
from .base import Context, is_company

RULE_ID = "P6.dob"

_SOURCE_ORDER = ("dobs", "profile_notes", "search_result")
_NEWS_REPORT = re.compile(r"^\s*According to\b")


def _all_held_dobs(person, primary) -> list[tuple[PartialDate | None, str, str]]:
    """Every DOB the bank holds for the person: the primary list first, then every
    approved identity check and every customer record (HoFC: a clearance must hold
    against all of them). Entries are (date or None if unreadable, source id, raw)."""
    out = list(primary)
    seen = {(sid, str(d) if d else raw) for d, sid, raw in out}

    def add(date, sid, raw):
        key = (sid, str(date) if date else raw)
        if key not in seen:
            seen.add(key)
            out.append((date, sid, raw))

    for ident in getattr(person, "identities", ()) or ():
        raw = (getattr(ident, "dob_raw", None) or "").strip()
        date = getattr(ident, "dob", None)
        if not isinstance(date, PartialDate):
            date = parse_date(raw) if raw else None
        if date is not None or raw:
            add(date, getattr(ident, "source_id", "") or "", raw or str(date))
    for date, sid, raw in _record_dobs(person):
        add(date, sid, raw)
    return out


def _news_age(cand) -> bool:
    """An age-derived birth year read from a news-report sentence."""
    return bool(cand.from_age) and bool(_NEWS_REPORT.match(cand.raw or ""))


# ------------------------------------------------------------------ customer side


def _customer_dobs(person) -> tuple[list[tuple[PartialDate | None, str, str]], bool]:
    """[(date or None if unreadable, source id, raw)], and whether it may clear.

    Verified DOB first (newest approved check). Its absence falls back to the
    customer records; an unreadable verified DOB stays unknown (never falls back).

    A Person without P4 identities (no `verified` attribute: resolver before #4)
    cannot tell whether an onboarding note verified a different DOB, so its DOB
    may agree but never clear (may_clear=False).
    """
    if not hasattr(person, "verified"):
        kyc = getattr(person, "latest_kyc", None)
        if kyc is not None and (getattr(kyc, "verified_dob", None) or "").strip():
            raw = kyc.verified_dob.strip()
            return [(parse_date(raw), kyc.check_id, raw)], False
        return _record_dobs(person), False
    verified = getattr(person, "verified", None)
    if verified is not None:
        date = getattr(verified, "dob", None)
        raw = (getattr(verified, "dob_raw", None) or "").strip()
        if not isinstance(date, PartialDate):
            date = parse_date(raw) if raw else None
        if date is not None or raw:
            return [(date, verified.source_id, raw or str(date))], True
    return _record_dobs(person), True


def _record_dobs(person) -> list[tuple[PartialDate | None, str, str]]:
    out: list[tuple[PartialDate | None, str, str]] = []
    seen: set[tuple[str, str]] = set()
    records = sorted(getattr(person, "records", ()) or (), key=lambda r: r.record_id)
    for rec in records:
        raw = (rec.dob or "").strip()
        if not raw:
            continue
        date = parse_date(raw)
        key = (str(date) if date else raw, rec.record_id)
        if key in seen:
            continue
        seen.add(key)
        out.append((date, rec.record_id, raw))
    return out


# ----------------------------------------------------------------------- hit side


def _cand_agrees(cust: PartialDate, cand) -> bool:
    if cand.date is not None:
        return dates_agree(cust, cand.date)
    return cust.year in cand.years


def _cand_contradicted(cust: PartialDate, cand) -> bool:
    if cand.date is not None:
        return dates_contradict(cust, cand.date)
    return bool(cand.years) and all(abs(cust.year - y) > 1 for y in cand.years)


def _cand_known(cand) -> bool:
    return cand.date is not None or bool(cand.years)


def _cand_text(cand) -> str:
    if cand.date is not None:
        return str(cand.date)
    return " or ".join(str(y) for y in cand.years)


def _hit_text(cands) -> str:
    return ", ".join(_cand_text(c) for c in cands if _cand_known(c))


def _source_clause(source: str, cands) -> str:
    texts = [_cand_text(c) for c in cands]
    joined = ", ".join(texts)
    if source == "dobs":
        if len(texts) == 1:
            return f"the hit's listed date of birth is {joined}"
        return f"the hit's listed dates of birth are {joined}"
    where = "profile notes" if source == "profile_notes" else "search results"
    exact = any(c.date is not None and c.date.month is not None for c in cands)
    what = "date of birth" if exact else "birth year"
    return f"the hit's {where} put its {what} at {joined}"


def _evidence(cust_ids: list[str], cands) -> tuple[str, ...]:
    """Customer-side ids, then the SR ids of search-result candidates that decided it."""
    ids = list(dict.fromkeys(cust_ids))
    srs = sorted({c.evidence for c in cands if c.source == "search_result" and c.evidence})
    return tuple(ids + [s for s in srs if s not in ids])


def _result(signal: Signal, summary: str, evidence=(), *, reason=None, weak=False) -> list[RuleResult]:
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.DOB,
            signal=signal,
            reason=reason,
            summary=summary,
            evidence=tuple(evidence),
            weak=weak,
            weak_why=WEAK_SEARCH_RESULT if weak else None,
        )
    ]


# ---------------------------------------------------------------------------- rule


def _check(ctx: Context) -> list[RuleResult]:
    person = ctx.person
    if person is None:
        return []  # no customer resolved: nothing to compare (the compiler says REVIEW)

    facts = ctx.facts
    records = getattr(person, "records", ()) or ()
    cust_type = (records[0].entity_type if records else None) or ctx.alert.entity_type
    company = is_company(cust_type)
    noun = "incorporation date" if company else "date of birth"

    cands = [c for c in facts.dob_candidates if not company or c.source == "dobs"]
    # HoFC call 8: ages inside news-report paragraphs are ignored.
    report = _NEWS_REPORT.match(first_paragraph(getattr(ctx.hit, "profile_notes", None)) or "")
    cands = [
        c for c in cands
        if not _news_age(c) and not (report and c.source == "profile_notes" and c.from_age)
    ]
    # R4: search results only when the hit gives no other birth date.
    if any(c.source != "search_result" for c in cands):
        cands = [c for c in cands if c.source != "search_result"]
    cands.sort(key=lambda c: _SOURCE_ORDER.index(c.source) if c.source in _SOURCE_ORDER else 9)
    known = [c for c in cands if _cand_known(c)]
    unreadable_hit = [c.raw for c in cands if not _cand_known(c)]
    if not company:
        unreadable_hit += list(facts.notes_dob_unparsed or ())

    cust, may_clear = _customer_dobs(person)
    readable = [(d, sid, raw) for d, sid, raw in cust if d is not None]

    if not cust:
        if not known:
            return _result(Signal.NEUTRAL, f"Neither the customer nor the hit has a known {noun}.")
        return _result(
            Signal.NEUTRAL,
            f"The customer's {noun} is not known, so it cannot be compared with the hit's {_hit_text(known)}.",
        )
    if not readable:
        d, sid, raw = cust[0]
        return _result(
            Signal.NEUTRAL,
            f"The customer's {noun} '{raw}' ({sid}) could not be read, so it is not compared.",
            (sid,),
        )
    shown = readable[0]
    cust_desc = f"{shown[0]} ({shown[1]})"
    if not known:
        if unreadable_hit:
            return _result(
                Signal.NEUTRAL,
                f"The customer's {noun} is {cust_desc}, but the hit's ({unreadable_hit[0]!r}) could not be read.",
                (shown[1],),
            )
        return _result(
            Signal.NEUTRAL,
            f"The customer's {noun} is {cust_desc} but the hit gives none, so it cannot be compared.",
            (shown[1],),
        )

    # Agreement: one customer DOB agrees with one hit birth date (P7).
    for d, sid, _raw in readable:
        agreeing = [c for c in known if _cand_agrees(d, c)]
        if not agreeing:
            continue
        # Another customer DOB on the person that every hit date contradicts: not safe to confirm.
        if any(all(_cand_contradicted(o, c) for c in known) for o, _s, _r in readable if o != d):
            break
        same_source = [c for c in known if c.source == agreeing[0].source]
        weak = all(c.source == "search_result" for c in agreeing)
        return _result(
            Signal.AGREEMENT,
            f"The customer's {noun} {d} ({sid}) agrees with the hit's {_hit_text(same_source)}.",
            _evidence([sid], agreeing),
            weak=weak,
        )

    held = _all_held_dobs(person, cust)
    held_unreadable = [(sid, raw) for d, sid, raw in held if d is None]
    all_contradicted = (
        may_clear
        and not held_unreadable
        and not unreadable_hit
        and all(_cand_contradicted(d, c) for d, _s, _r in held for c in known)
    )
    if all_contradicted:
        by_source: dict[str, list] = {}
        for c in known:
            by_source.setdefault(c.source, []).append(c)
        clauses = " and ".join(_source_clause(s, by_source[s]) for s in by_source)
        cust_ids = [sid for _d, sid, _r in readable]
        cust_text = f"{shown[0]} ({shown[1]})" if len(readable) == 1 else ", ".join(
            f"{d} ({sid})" for d, sid, _r in readable
        )
        weak = all(c.source == "search_result" for c in known)
        return _result(
            Signal.CONTRADICTION,
            f"The customer's {noun} is {cust_text} but {clauses}.",
            _evidence(cust_ids, known),
            reason=ReasonCode.DOB_MISMATCH,
            weak=weak,
        )

    if unreadable_hit:
        why = f"part of the hit's birth information ({unreadable_hit[0]!r}) could not be read"
    elif held_unreadable:
        sid, raw = held_unreadable[0]
        why = f"one of the customer's dates of birth ({raw!r}, {sid}) could not be read"
    elif not may_clear:
        why = "the customer's verified identity is not available, so the date cannot clear the hit"
    elif any(
        not _cand_contradicted(d, c) for d, _s, _r in held[len(readable):] for c in known
    ):
        why = "another date of birth the bank holds for the customer does not contradict the hit's"
    else:
        why = "the dates are too close to call (one year apart) or the customer's records disagree"
    return _result(
        Signal.NEUTRAL,
        f"The customer's {noun} {cust_desc} neither agrees with nor clearly contradicts "
        f"the hit's {_hit_text(known)}: {why}.",
        (shown[1],),
    )


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # noqa: BLE001 - messy data must never break a run; abstain
        return []
