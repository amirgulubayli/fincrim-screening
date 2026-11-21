"""Shared data contracts for the whole team.

STANDARD LIBRARY ONLY (the brief requires it for everything under system/).
Changing a public name or field here is a *contract change*: open the PR with the
`contract-change` label and say who is affected (see AGENTS.md).

Input records mirror the files in the data folder (alerts.jsonl, hits.jsonl, ...).
Values are kept RAW (as the provider wrote them); normalisation lives in
screening.normalize.*. Unknown keys are preserved in `extra` so a new column in
the unseen set never crashes the loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------- enums


class Decision(str, Enum):
    DIFFERENT_ENTITY = "DIFFERENT_ENTITY"  # hit cleared
    SAME_ENTITY = "SAME_ENTITY"  # goes to compliance
    REVIEW = "REVIEW"  # a person looks at it


class ReasonCode(str, Enum):
    TEMPORAL_IMPOSSIBILITY = "TEMPORAL_IMPOSSIBILITY"
    DOB_MISMATCH = "DOB_MISMATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    NAME_MISMATCH_VERIFIED = "NAME_MISMATCH_VERIFIED"
    ENTITY_TYPE_MISMATCH = "ENTITY_TYPE_MISMATCH"
    DECEASED = "DECEASED"
    NAME_DOB_LOCATION_MATCH = "NAME_DOB_LOCATION_MATCH"
    NAME_DOB_MATCH = "NAME_DOB_MATCH"
    NAME_LOCATION_MATCH = "NAME_LOCATION_MATCH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    AMBIGUOUS_CUSTOMER_RECORD = "AMBIGUOUS_CUSTOMER_RECORD"


REASONS_BY_DECISION: dict[Decision, frozenset[ReasonCode]] = {
    Decision.DIFFERENT_ENTITY: frozenset(
        {
            ReasonCode.TEMPORAL_IMPOSSIBILITY,
            ReasonCode.DOB_MISMATCH,
            ReasonCode.LOCATION_MISMATCH,
            ReasonCode.NAME_MISMATCH_VERIFIED,
            ReasonCode.ENTITY_TYPE_MISMATCH,
            ReasonCode.DECEASED,
        }
    ),
    Decision.SAME_ENTITY: frozenset(
        {
            ReasonCode.NAME_DOB_LOCATION_MATCH,
            ReasonCode.NAME_DOB_MATCH,
            ReasonCode.NAME_LOCATION_MATCH,
        }
    ),
    Decision.REVIEW: frozenset(
        {ReasonCode.INSUFFICIENT_DATA, ReasonCode.AMBIGUOUS_CUSTOMER_RECORD}
    ),
}


class Tier(str, Enum):
    """Human-in-the-loop routing. Not scored; drives the review dashboard."""

    AUTO_CLEAR = "AUTO_CLEAR"  # DIFFERENT_ENTITY on a hard, well-evidenced contradiction
    AUTO_ESCALATE = "AUTO_ESCALATE"  # SAME_ENTITY on strong agreement -> compliance team
    BORDERLINE = "BORDERLINE"  # automated decision that rests on weak evidence -> 4-eyes QA
    HUMAN_REVIEW = "HUMAN_REVIEW"  # SOP says REVIEW -> analyst queue


class Signal(str, Enum):
    CONTRADICTION = "contradiction"  # fact that clears the hit (SOP principle 6)
    AGREEMENT = "agreement"  # fact that supports confirming (SOP principle 7)
    NEUTRAL = "neutral"  # checked, nothing decisive (still shown in the trace)


class Facet(str, Enum):
    TYPE = "type"
    DECEASED = "deceased"
    TIME = "time"
    DOB = "dob"
    LOCATION = "location"
    NAME = "name"


CUSTOMER_AMBIGUOUS = "AMBIGUOUS"  # value of customer_record_id when >1 person fits

# ------------------------------------------------------------------- input records

# Fields that can hold several values. The data-room JSONL may give lists; the
# workbook export gives "a; b" strings. Both are accepted.
_LIST_FIELDS = {
    "hit_ids",
    "aliases",
    "dobs",
    "nationality",
    "citizenship",
    "countries",
    "addresses",
    "identifiers",
    "categories",
    "sources",
    "source_urls",
    "reason_codes",
    "customer_record_ids",
    "evidence",
}


def as_list(value: Any) -> tuple[str, ...]:
    """Normalise a multi-valued raw field to a tuple of non-empty strings."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if v is not None and str(v).strip())
    text = str(value).strip()
    if not text:
        return ()
    return tuple(part.strip() for part in text.split(";") if part.strip())


@dataclass(frozen=True)
class _Record:
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]):
        known = {f.name for f in fields(cls)} - {"extra"}
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in raw.items():
            if key in known:
                kwargs[key] = as_list(value) if key in _LIST_FIELDS else _scalar(value)
            else:
                extra[key] = value
        return cls(extra=extra, **kwargs)


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


@dataclass(frozen=True)
class Alert(_Record):
    alert_id: str = ""
    screened_at: str | None = None
    customer_name: str | None = None
    entity_type: str | None = None  # "Individual" | "Company"
    dob: str | None = None  # for companies: incorporation date
    country: str | None = None
    city: str | None = None
    account_ref: str | None = None
    login_handle: str | None = None
    email: str | None = None
    hit_count: Any = None
    hit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit(_Record):
    hit_id: str = ""
    alert_id: str = ""
    match_score: Any = None
    entity_type: str | None = None
    name: str | None = None
    aliases: tuple[str, ...] = ()
    gender: str | None = None
    dobs: tuple[str, ...] = ()
    place_of_birth: str | None = None
    life_status: str | None = None
    deceased_date: str | None = None
    nationality: tuple[str, ...] = ()
    citizenship: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    primary_category: str | None = None
    position_or_allegation: str | None = None
    details: str | None = None
    relationships: str | None = None
    profile_notes: str | None = None
    sources: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Customer(_Record):
    record_id: str = ""
    entity_type: str | None = None
    full_name: str | None = None
    preferred_name: str | None = None
    login_name: str | None = None
    dob: str | None = None
    nationality: tuple[str, ...] = ()
    residence_country: str | None = None
    city: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    occupation: str | None = None
    employer: str | None = None
    registration_number: str | None = None
    onboarded_at: str | None = None
    status: str | None = None
    last_kyc_refresh: str | None = None


@dataclass(frozen=True)
class Account(_Record):
    account_id: str = ""
    customer_id: str = ""
    login_handle: str | None = None
    phone: str | None = None
    opened_at: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class Onboarding(_Record):
    account_id: str = ""
    customer_id: str = ""
    kyc_applicant_id: str | None = None
    onboarded_at: str | None = None


@dataclass(frozen=True)
class KycCheck(_Record):
    check_id: str = ""
    applicant_id: str = ""
    provider: str | None = None
    verified_name: str | None = None
    verified_dob: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    document_country: str | None = None
    result: str | None = None  # only "approved" is evidence
    checked_at: str | None = None


@dataclass(frozen=True)
class Note(_Record):
    note_id: str = ""
    requester_email: str | None = None
    created_at: str | None = None
    channel: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class SearchResult(_Record):
    result_id: str = ""
    query: str | None = None  # the hit name's lowercase words, alphabetical
    title: str | None = None
    snippet: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class PracticeLabel(_Record):
    hit_id: str = ""
    alert_id: str = ""
    decision: str | None = None
    reason_codes: tuple[str, ...] = ()  # any of these is accepted
    customer_record_ids: tuple[str, ...] = ()  # any of these is accepted
    evidence: tuple[str, ...] = ()
    reference_reason: str | None = None
    path: str | None = None


# ------------------------------------------------------------------ rule outputs


@dataclass(frozen=True)
class RuleResult:
    """One SOP check's verdict for one (hit, person) pair.

    rule_id   stable id of the check, e.g. "P6.dob" (principle 6, DOB clause)
    facet     which identity facet it speaks to
    signal    contradiction / agreement / neutral
    reason    the reason code this result would justify (None for neutral)
    summary   one plain-English sentence with the decisive facts
    evidence  record IDs the check relied on (hit, customer, KYC, NOTE, SR ...)
    weak      True when the fact is shaky -> routes automated decisions to BORDERLINE
    weak_why  short machine-readable flag, e.g. "DOB_FROM_SEARCH_RESULT"
    """

    rule_id: str
    facet: Facet
    signal: Signal
    summary: str
    reason: ReasonCode | None = None
    evidence: tuple[str, ...] = ()
    weak: bool = False
    weak_why: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "facet": self.facet.value,
            "signal": self.signal.value,
            "reason": self.reason.value if self.reason else None,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "weak": self.weak,
            "weak_why": self.weak_why,
        }


# ----------------------------------------------------------------- final decision

SUBMISSION_FIELDS = (
    "hit_id",
    "alert_id",
    "decision",
    "reason_code",
    "customer_record_id",
    "reason",
    "evidence",
)


@dataclass(frozen=True)
class HitDecision:
    hit_id: str
    alert_id: str
    decision: Decision
    reason_code: ReasonCode
    customer_record_id: str | None  # record id, "AMBIGUOUS", or None
    reason: str  # 2-4 sentences naming the decisive facts
    evidence: tuple[str, ...]  # IDs that DECIDED it, not everything looked at
    # ---- not scored: human-in-the-loop + explainability
    tier: Tier = Tier.HUMAN_REVIEW
    flags: tuple[str, ...] = ()
    trace: tuple[RuleResult, ...] = ()

    def submission(self) -> dict[str, Any]:
        """Exactly the fields the organisers score, in a fixed order."""
        return {
            "hit_id": self.hit_id,
            "alert_id": self.alert_id,
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "customer_record_id": self.customer_record_id,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }

    def full(self) -> dict[str, Any]:
        out = self.submission()
        out["tier"] = self.tier.value
        out["flags"] = list(self.flags)
        out["trace"] = [r.to_dict() for r in self.trace]
        return out
