from __future__ import annotations

from dataclasses import dataclass

from ..contracts import Alert, Hit
from ..facts import HitFacts
from ..loaders import Dataset
from ..resolve import Person


@dataclass(frozen=True)
class Context:
    ds: Dataset
    alert: Alert
    hit: Hit
    facts: HitFacts
    person: Person | None  # None when no customer record was found

    @property
    def customer_is_company(self) -> bool:
        etype = (self.person.records[0].entity_type if self.person else None) or self.alert.entity_type
        return is_company(etype)


def is_company(entity_type: str | None) -> bool:
    return (entity_type or "").strip().lower() in {
        "company", "organisation", "organization", "entity", "business", "corporate"
    }


def is_person(entity_type: str | None) -> bool:
    return (entity_type or "").strip().lower() in {"individual", "person", "natural person"}
