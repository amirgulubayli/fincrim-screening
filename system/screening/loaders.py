"""Load a data folder (dev/ or the unseen set) into an indexed, read-only Dataset.

Standard library only. The system must read ONLY the folder it is given.
Iteration order is always the file order, and every public listing is sorted by
ID, so runs are reproducible regardless of PYTHONHASHSEED.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    Account,
    Alert,
    Customer,
    Hit,
    KycCheck,
    Note,
    Onboarding,
    PracticeLabel,
    SearchResult,
)

FILES = {
    "alerts": "alerts.jsonl",
    "hits": "hits.jsonl",
    "customers": "customers.jsonl",
    "accounts": "accounts.jsonl",
    "onboarding": "onboarding.jsonl",
    "kyc_checks": "kyc_checks.jsonl",
    "notes": "notes.jsonl",
    "search_results": "search_results.jsonl",
    "practice_labels": "practice_labels.jsonl",  # dev only
}
REQUIRED = ("alerts", "hits")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:  # keep going; report clearly
                raise ValueError(f"{path.name}:{line_no}: bad JSON ({exc})") from exc
    return rows


def _key(value: str | None) -> str:
    return (value or "").strip().lower()


@dataclass
class Dataset:
    root: Path
    alerts: dict[str, Alert] = field(default_factory=dict)
    hits: dict[str, Hit] = field(default_factory=dict)
    customers: dict[str, Customer] = field(default_factory=dict)
    accounts: list[Account] = field(default_factory=list)  # account_id repeats for joint holders
    onboarding: list[Onboarding] = field(default_factory=list)
    kyc_checks: dict[str, KycCheck] = field(default_factory=dict)
    notes: dict[str, Note] = field(default_factory=dict)
    search_results: dict[str, SearchResult] = field(default_factory=dict)
    practice_labels: dict[str, PracticeLabel] = field(default_factory=dict)
    name_variants: dict[str, Any] = field(default_factory=dict)
    policy_text: str = ""

    # ---- indexes (built once in build_indexes)
    accounts_by_id: dict[str, list[Account]] = field(default_factory=dict)
    accounts_by_login: dict[str, list[Account]] = field(default_factory=dict)
    accounts_by_customer: dict[str, list[Account]] = field(default_factory=dict)
    customers_by_email: dict[str, list[Customer]] = field(default_factory=dict)
    customers_by_phone: dict[str, list[Customer]] = field(default_factory=dict)
    onboarding_by_account: dict[str, list[Onboarding]] = field(default_factory=dict)
    kyc_by_applicant: dict[str, list[KycCheck]] = field(default_factory=dict)
    notes_by_email: dict[str, list[Note]] = field(default_factory=dict)
    search_by_query: dict[str, list[SearchResult]] = field(default_factory=dict)

    def build_indexes(self) -> None:
        idx: dict[str, dict[str, list]] = {
            name: defaultdict(list)
            for name in (
                "accounts_by_id",
                "accounts_by_login",
                "accounts_by_customer",
                "customers_by_email",
                "customers_by_phone",
                "onboarding_by_account",
                "kyc_by_applicant",
                "notes_by_email",
                "search_by_query",
            )
        }
        for acc in self.accounts:
            idx["accounts_by_id"][acc.account_id].append(acc)
            if acc.login_handle:
                idx["accounts_by_login"][_key(acc.login_handle)].append(acc)
            idx["accounts_by_customer"][acc.customer_id].append(acc)
        for cus in self.customers.values():
            if cus.email:
                idx["customers_by_email"][_key(cus.email)].append(cus)
            if cus.phone:
                idx["customers_by_phone"][_key(cus.phone)].append(cus)
        for ob in self.onboarding:
            idx["onboarding_by_account"][ob.account_id].append(ob)
        for chk in self.kyc_checks.values():
            idx["kyc_by_applicant"][chk.applicant_id].append(chk)
        for note in self.notes.values():
            if note.requester_email:
                idx["notes_by_email"][_key(note.requester_email)].append(note)
        for res in self.search_results.values():
            if res.query:
                idx["search_by_query"][_key(res.query)].append(res)
        for name, table in idx.items():
            setattr(self, name, dict(table))

    def summary(self) -> dict[str, int]:
        return {
            "alerts": len(self.alerts),
            "hits": len(self.hits),
            "customers": len(self.customers),
            "accounts": len(self.accounts),
            "onboarding": len(self.onboarding),
            "kyc_checks": len(self.kyc_checks),
            "notes": len(self.notes),
            "search_results": len(self.search_results),
            "practice_labels": len(self.practice_labels),
        }


def load_dataset(root: str | Path) -> Dataset:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"data folder not found: {root}")
    for name in REQUIRED:
        if not (root / FILES[name]).exists():
            raise FileNotFoundError(f"missing {FILES[name]} in {root}")

    def rows(name: str) -> list[dict[str, Any]]:
        path = root / FILES[name]
        return read_jsonl(path) if path.exists() else []

    ds = Dataset(root=root)
    ds.alerts = {r.alert_id: r for r in map(Alert.from_raw, rows("alerts"))}
    ds.hits = {r.hit_id: r for r in map(Hit.from_raw, rows("hits"))}
    ds.customers = {r.record_id: r for r in map(Customer.from_raw, rows("customers"))}
    ds.accounts = [Account.from_raw(r) for r in rows("accounts")]
    ds.onboarding = [Onboarding.from_raw(r) for r in rows("onboarding")]
    ds.kyc_checks = {r.check_id: r for r in map(KycCheck.from_raw, rows("kyc_checks"))}
    ds.notes = {r.note_id: r for r in map(Note.from_raw, rows("notes"))}
    ds.search_results = {
        r.result_id: r for r in map(SearchResult.from_raw, rows("search_results"))
    }
    ds.practice_labels = {
        r.hit_id: r for r in map(PracticeLabel.from_raw, rows("practice_labels"))
    }
    variants = root / "name_variants.json"
    if variants.exists():
        ds.name_variants = json.loads(variants.read_text(encoding="utf-8"))
    policy = root / "POLICY.md"
    if policy.exists():
        ds.policy_text = policy.read_text(encoding="utf-8")
    ds.build_indexes()
    return ds
