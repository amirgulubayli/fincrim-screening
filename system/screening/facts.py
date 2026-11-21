"""Gather a hit's facts wherever they are (SOP principle 5).

Implemented: names + aliases, raw DOBs from the `dobs` field, countries tied to
the hit (countries, nationality, citizenship, last part of each address,
place_of_birth), deceased date, search-result lookup key.
TODO seed issue "Hit facts from profile notes and search results":
  * DOB sentences / ages-at-date in the FIRST paragraph of profile_notes
  * dated reports: lines of the exact form "According to ...; September 10, 2025: ..."
  * search results that are about THIS hit: same kind of listing (from the primary
    category) in a country tied to the hit; most results are namesakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Hit, SearchResult
from .loaders import Dataset
from .normalize.names import name_parts

# Listing phrase per category, used to decide if a search result is about the hit.
LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
DEFAULT_LISTING_PHRASE = "named on a watchlist"


@dataclass(frozen=True)
class HitFacts:
    hit_id: str
    names: tuple[str, ...]  # primary name first, then aliases
    dobs_raw: tuple[str, ...]  # as the provider wrote them
    dob_sources: tuple[str, ...]  # parallel to dobs_raw: "dobs" | "profile_notes" | "SR-xxxx"
    countries_raw: tuple[str, ...]
    deceased_date: str | None
    listing_phrase: str
    search_results: tuple[SearchResult, ...]  # candidates only; NOT yet filtered to this hit


def search_query_key(name: str | None) -> str:
    """Search results are filed under the name's lowercase words in alphabetical order."""
    return " ".join(sorted(name_parts(name)))


def listing_phrase(hit: Hit) -> str:
    category = (hit.primary_category or (hit.categories[0] if hit.categories else "") or "")
    return LISTING_PHRASES.get(category.strip().lower(), DEFAULT_LISTING_PHRASE)


def gather_hit_facts(ds: Dataset, hit: Hit) -> HitFacts:
    names = tuple(n for n in ((hit.name,) + tuple(hit.aliases)) if n)
    countries: list[str] = []
    for value in (*hit.countries, *hit.nationality, *hit.citizenship):
        countries.append(value)
    for address in hit.addresses:
        last = address.split("[")[0].rstrip().rstrip(",").split(",")[-1].strip()
        if last:
            countries.append(last)
    if hit.place_of_birth:
        countries.append(hit.place_of_birth.split(",")[-1].strip())
    results = ds.search_by_query.get(search_query_key(hit.name), [])
    return HitFacts(
        hit_id=hit.hit_id,
        names=names,
        dobs_raw=tuple(hit.dobs),
        dob_sources=tuple("dobs" for _ in hit.dobs),
        countries_raw=tuple(dict.fromkeys(countries)),
        deceased_date=hit.deceased_date,
        listing_phrase=listing_phrase(hit),
        search_results=tuple(sorted(results, key=lambda r: r.result_id)),
    )
