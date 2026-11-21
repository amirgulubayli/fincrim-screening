"""Customer resolution: who is the customer behind an alert? (SOP principles 2-4)

Output is a Resolution with zero, one or several *persons*. A person is a group
of customer records that belong to the same human ("one person, one picture"),
with their approved KYC checks (newest first) and their notes.

Implemented: link following (account_ref / login_handle / email), KYC lookup
through onboarding, notes lookup by email.
TODO seed issues:
  * "Customer resolution: unlinked alerts" - find records that fit the screened
    name / DOB / country / city when the alert has no link.
  * "Customer resolution: candidate filtering" - a link can lead to several people
    (joint accounts); keep only those the screened details fit.
  * "Identity graph" - merge records sharing phone + full DOB, or the same document
    number on approved checks, transitively.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Alert, Customer, KycCheck, Note
from .loaders import Dataset


@dataclass(frozen=True)
class Person:
    records: tuple[Customer, ...]
    kyc: tuple[KycCheck, ...] = ()  # approved checks only, newest first
    notes: tuple[Note, ...] = ()

    @property
    def key(self) -> str:
        return min(r.record_id for r in self.records)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(sorted(r.record_id for r in self.records))

    @property
    def latest_kyc(self) -> KycCheck | None:
        return self.kyc[0] if self.kyc else None


@dataclass(frozen=True)
class Resolution:
    persons: tuple[Person, ...]
    method: str  # "account_ref" | "login_handle" | "email" | "unlinked" | "none"
    path: tuple[str, ...] = field(default_factory=tuple)  # human-readable steps (for the UI)

    @property
    def ambiguous(self) -> bool:
        return len(self.persons) > 1


def _approved_kyc(ds: Dataset, customer: Customer) -> list[KycCheck]:
    checks: list[KycCheck] = []
    for acc in ds.accounts_by_customer.get(customer.record_id, []):
        for ob in ds.onboarding_by_account.get(acc.account_id, []):
            if ob.customer_id != customer.record_id or not ob.kyc_applicant_id:
                continue
            for chk in ds.kyc_by_applicant.get(ob.kyc_applicant_id, []):
                if (chk.result or "").strip().lower() == "approved":
                    checks.append(chk)
    # newest first; ties broken by id for determinism
    # TODO(date parser): checked_at formats vary; ISO sorts correctly as text.
    return sorted(set(checks), key=lambda c: (c.checked_at or "", c.check_id), reverse=True)


def _notes(ds: Dataset, customer: Customer) -> list[Note]:
    if not customer.email:
        return []
    found = ds.notes_by_email.get(customer.email.strip().lower(), [])
    return sorted(found, key=lambda n: (n.created_at or "", n.note_id))


def build_person(ds: Dataset, records: list[Customer]) -> Person:
    kyc: list[KycCheck] = []
    notes: list[Note] = []
    for rec in records:
        kyc.extend(_approved_kyc(ds, rec))
        notes.extend(_notes(ds, rec))
    kyc = sorted(set(kyc), key=lambda c: (c.checked_at or "", c.check_id), reverse=True)
    notes = sorted(set(notes), key=lambda n: (n.created_at or "", n.note_id))
    return Person(records=tuple(sorted(records, key=lambda r: r.record_id)),
                  kyc=tuple(kyc), notes=tuple(notes))


def _linked_customers(ds: Dataset, alert: Alert) -> tuple[list[Customer], str, list[str]]:
    path: list[str] = []
    if alert.account_ref:
        accounts = ds.accounts_by_id.get(alert.account_ref, [])
        ids = sorted({a.customer_id for a in accounts})
        path.append(f"account_ref {alert.account_ref} -> accounts -> {', '.join(ids) or 'none'}")
        return [ds.customers[i] for i in ids if i in ds.customers], "account_ref", path
    if alert.login_handle:
        accounts = ds.accounts_by_login.get(alert.login_handle.strip().lower(), [])
        ids = sorted({a.customer_id for a in accounts})
        path.append(f"login_handle {alert.login_handle} -> accounts -> {', '.join(ids) or 'none'}")
        return [ds.customers[i] for i in ids if i in ds.customers], "login_handle", path
    if alert.email:
        found = ds.customers_by_email.get(alert.email.strip().lower(), [])
        ids = sorted({c.record_id for c in found})
        path.append(f"email {alert.email} -> customers -> {', '.join(ids) or 'none'}")
        return [ds.customers[i] for i in ids], "email", path
    return [], "unlinked", ["no link on the alert"]


def resolve_customer(ds: Dataset, alert: Alert) -> Resolution:
    customers, method, path = _linked_customers(ds, alert)
    if method == "unlinked":
        # TODO seed issue: search customers by screened name/DOB/country/city.
        return Resolution(persons=(), method="unlinked", path=tuple(path))
    # TODO seed issue: filter candidates by screened details; merge via identity graph.
    persons = tuple(build_person(ds, [c]) for c in customers)
    return Resolution(persons=persons, method=method if persons else "none", path=tuple(path))
