"""P6 Time: customer younger than 16 at the earliest dated report, or company
incorporated after it -> TEMPORAL_IMPOSSIBILITY.

Dated reports are ONLY lines like 'According to ...; September 10, 2025: ...'
(facts.earliest_dated_report). Both checks use the conservative edge so a partial
date can never manufacture a contradiction:

* person: the OLDEST the customer could have been at the report (earliest possible
  birth date: a year-only DOB counts from Jan 1, a year-month DOB from the 1st,
  against the latest possible report date). Contradiction only if even then they
  were under 16.
* company: the earliest possible incorporation date must fall strictly after the
  latest possible report date (normalize.dates.definitely_before).

Customer DOB: the verified DOB (Person.verified_dob_date, source
verified_source_id) when the resolver provides one, else every customer record's
DOB; with several, every one must give the contradiction.
"""

from __future__ import annotations

import calendar
import datetime as _dt

from ..contracts import Facet, ReasonCode, RuleResult, Signal
from ..normalize.dates import PartialDate, definitely_before, parse_date
from .base import Context

RULE_ID = "P6.temporal"
MIN_AGE = 16


def check(ctx: Context) -> list[RuleResult]:
    try:
        return _check(ctx)
    except Exception:  # noqa: BLE001 - messy data must never break a run; abstain
        return []


def _check(ctx: Context) -> list[RuleResult]:
    report = ctx.facts.earliest_dated_report
    if report is None or report.date is None:
        return []
    company = ctx.customer_is_company
    dobs = _customer_dobs(ctx, company)
    if not dobs:
        return []
    if company:
        return _company(report.date, dobs)
    return _person(report.date, dobs)


def _customer_dobs(ctx: Context, company: bool) -> list[tuple[PartialDate, str]]:
    """(date, source id) pairs for the customer's DOB / incorporation date, sorted by id."""
    person = ctx.person
    if person is not None:
        verified = parse_date(getattr(person, "verified_dob_date", None))
        if verified is not None:
            source = getattr(person, "verified_source_id", None) or person.key
            return [(verified, str(source))]
        out = []
        for record in sorted(person.records, key=lambda r: r.record_id):
            d = parse_date(record.dob)
            if d is not None:
                out.append((d, record.record_id))
        if out:
            return out
    if company:
        d = parse_date(ctx.alert.dob)
        if d is not None:
            ids = person.record_ids if person else ()
            return [(d, ids[0] if ids else ctx.alert.alert_id)]
    return []


def _earliest(d: PartialDate) -> _dt.date:
    month, day = d.month or 1, d.day or 1
    if (month, day) == (2, 29):
        day = 28  # conservative: count the birthday as early as possible
    return _dt.date(d.year, month, day)


def _latest(d: PartialDate) -> _dt.date:
    month = d.month or 12
    day = d.day or calendar.monthrange(d.year, month)[1]
    return _dt.date(d.year, month, day)


def _max_age(birth: PartialDate, at: PartialDate) -> int:
    b, a = _earliest(birth), _latest(at)
    return a.year - b.year - ((a.month, a.day) < (b.month, b.day))


def _person(at: PartialDate, dobs: list[tuple[PartialDate, str]]) -> list[RuleResult]:
    aged = [(_max_age(d, at), d, src) for d, src in dobs]
    oldest_age, oldest_dob, oldest_src = max(aged, key=lambda x: (x[0], x[2]))
    if oldest_age < MIN_AGE:
        if oldest_age < 0:
            age_txt = "not yet born"
        elif oldest_dob.day is not None:
            age_txt = str(oldest_age)
        else:
            age_txt = f"at most {oldest_age}"
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.TIME,
                signal=Signal.CONTRADICTION,
                reason=ReasonCode.TEMPORAL_IMPOSSIBILITY,
                summary=f"The customer (born {oldest_dob}, {oldest_src}) was {age_txt} at the "
                f"earliest dated report ({at}).",
                evidence=tuple(sorted({src for _, _, src in aged})),
            )
        ]
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.TIME,
            signal=Signal.NEUTRAL,
            summary=f"The customer (born {oldest_dob}) could have been {MIN_AGE} or older at the "
            f"earliest dated report ({at}).",
            evidence=(),
        )
    ]


def _company(at: PartialDate, dobs: list[tuple[PartialDate, str]]) -> list[RuleResult]:
    if all(definitely_before(at, d) for d, _ in dobs):
        inc, src = min(dobs, key=lambda x: (_earliest(x[0]), x[1]))
        return [
            RuleResult(
                rule_id=RULE_ID,
                facet=Facet.TIME,
                signal=Signal.CONTRADICTION,
                reason=ReasonCode.TEMPORAL_IMPOSSIBILITY,
                summary=f"The customer company was incorporated {'on' if inc.day else 'in'} "
                f"{inc} ({src}), after the "
                f"earliest dated report ({at}).",
                evidence=tuple(sorted({s for _, s in dobs})),
            )
        ]
    return [
        RuleResult(
            rule_id=RULE_ID,
            facet=Facet.TIME,
            signal=Signal.NEUTRAL,
            summary=f"The customer company may have existed at the earliest dated report ({at}).",
            evidence=(),
        )
    ]
