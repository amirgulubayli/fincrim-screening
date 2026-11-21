"""Country normalisation: codes, names and nationalities -> one canonical name.

"International" and "Europe" are not countries. Territories count as their own
country. TODO (seed issue "Country normaliser"): fill the table from the data
(customers.residence_country/nationality, hits.countries/nationality/addresses,
kyc_checks.document_country) - run `python tools/profile_values.py` to list them.
"""

from __future__ import annotations

from .names import strip_accents

NOT_COUNTRIES = frozenset({"international", "europe", "global", "worldwide", "unknown", "n/a"})

# alias (lowercase, no accents) -> canonical country name. Extend freely.
_ALIASES: dict[str, str] = {
    "gb": "United Kingdom", "gbr": "United Kingdom", "uk": "United Kingdom",
    "united kingdom": "United Kingdom", "british": "United Kingdom", "great britain": "United Kingdom",
    "us": "United States", "usa": "United States", "united states": "United States",
    "united states of america": "United States", "american": "United States",
    "ng": "Nigeria", "nga": "Nigeria", "nigeria": "Nigeria", "nigerian": "Nigeria",
    "de": "Germany", "deu": "Germany", "germany": "Germany", "german": "Germany",
    "es": "Spain", "esp": "Spain", "spain": "Spain", "spanish": "Spain",
    "br": "Brazil", "bra": "Brazil", "brazil": "Brazil", "brazilian": "Brazil",
    "ca": "Canada", "can": "Canada", "canada": "Canada", "canadian": "Canada",
}


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    key = strip_accents(str(raw)).strip().lower().rstrip(".")
    if not key or key in NOT_COUNTRIES:
        return None
    return _ALIASES.get(key, str(raw).strip().title())
