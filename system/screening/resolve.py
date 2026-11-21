"""Customer resolution: who is the customer behind an alert? (SOP principles 2-4)

Output is a Resolution with zero, one or several *persons*. A person is a group
of customer records that belong to the same human ("one person, one picture"),
with their approved KYC checks (newest first), their notes, their verified identity
and every country tied to them.

P2  Follow the alert's link (account_ref / login_handle / email). A link that leads to
    more than one person (joint accounts) is narrowed by the screened details, as in
    a search without a link. Without a link, search all customer records for those
    that fit the screened details:
      * a full, preferred or login name that agrees with the screened name (P8),
      * a date of birth equal to the screened one in every part both give,
      * the screened country as residence or nationality,
      * the screened city in the record's city or address.
    A detail that is missing on either side never excludes a record.
    Choice when the screened details fit NONE of the people a link leads to: the SOP
    says the details pick "as in a search without a link", and such a search that
    finds nobody has found nobody. So the resolution is empty (method "none",
    customer_record_id null): the hit goes to REVIEW and no holder's facts are used
    for a clearance - "never use another customer's records" outweighs the cost of
    a needless review. (Never happens on dev: every joint alert fits one holder.)
    A link to a single person is followed as is; the details are not re-checked.
P3  Every candidate record is expanded to its whole identity group (identity.py):
    same phone AND same full DOB, or the same document number on approved checks,
    transitively.
P4  Person.verified is the most recent approved identity check, from kyc_checks or
    from an onboarding-team note; rejected / expired checks are never evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contracts import Alert, Customer, KycCheck, Note
from .identity import (
    CountryClaim,
    MergeEdge,
    PartialDate,
    VerifiedIdentity,
    canonical_country,
    country_claims,
    dates_compatible,
    identity_graph,
    parse_dob,
    verified_identities,
)
from .loaders import Dataset
from .normalize.names import name_parts, strip_accents

try:
    from .normalize.names import PLACEHOLDERS
except ImportError:  # pragma: no cover - renamed upstream
    PLACEHOLDERS = frozenset({"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbc"})


@dataclass(frozen=True)
class Person:
    records: tuple[Customer, ...]
    kyc: tuple[KycCheck, ...] = ()  # approved checks only, newest first
    notes: tuple[Note, ...] = ()
    # approved identity checks from kyc_checks AND onboarding notes, newest first
    identities: tuple[VerifiedIdentity, ...] = ()
    # every country tied to the person, raw, with the id of the record that says so
    countries: tuple[CountryClaim, ...] = ()
    # why the records were merged into one person (empty for a single record)
    merges: tuple[MergeEdge, ...] = ()

    @property
    def key(self) -> str:
        return min(r.record_id for r in self.records)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(sorted(r.record_id for r in self.records))

    @property
    def latest_kyc(self) -> KycCheck | None:
        return self.kyc[0] if self.kyc else None

    # ---- P4 verified identity (most recent approved check: KYC-... or NOTE-...)
    @property
    def verified(self) -> VerifiedIdentity | None:
        return self.identities[0] if self.identities else None

    @property
    def verified_name(self) -> str | None:
        return self.verified.name if self.verified else None

    @property
    def verified_dob(self) -> str | None:
        """Raw DOB as the verifying source wrote it (parse with normalize.dates)."""
        return self.verified.dob_raw if self.verified else None

    @property
    def verified_dob_date(self) -> PartialDate | None:
        return self.verified.dob if self.verified else None

    @property
    def verified_document_country(self) -> str | None:
        return self.verified.document_country if self.verified else None

    @property
    def verified_source_id(self) -> str | None:
        return self.verified.source_id if self.verified else None

    @property
    def kyc_notes(self) -> tuple[VerifiedIdentity, ...]:
        return tuple(v for v in self.identities if v.kind == "note")

    # ---- P6 location: places the notes say they lived
    @property
    def note_countries(self) -> tuple[CountryClaim, ...]:
        return tuple(c for c in self.countries if c.kind == "note_residence")

    @property
    def country_values(self) -> tuple[str, ...]:
        """Distinct raw country strings tied to the person (records, documents, notes)."""
        return tuple(dict.fromkeys(c.raw for c in self.countries))


@dataclass(frozen=True)
class Resolution:
    persons: tuple[Person, ...]
    method: str  # "account_ref" | "login_handle" | "email" | "unlinked" | "none"
    path: tuple[str, ...] = field(default_factory=tuple)  # human-readable steps (for the UI)
    candidates: tuple[str, ...] = ()  # record ids the link / search produced, before filtering

    @property
    def ambiguous(self) -> bool:
        return len(self.persons) > 1


# ------------------------------------------------------------------ building persons


def _notes_for(ds: Dataset, customer: Customer) -> list[Note]:
    if not customer.email:
        return []
    return list(ds.notes_by_email.get(customer.email.strip().lower(), []))


def build_person(ds: Dataset, records: list[Customer]) -> Person:
    graph = identity_graph(ds)
    kyc: dict[str, KycCheck] = {}
    notes: dict[str, Note] = {}
    for rec in records:
        for chk in graph.approved_by_customer.get(rec.record_id, ()):
            kyc[chk.check_id] = chk
        for note in _notes_for(ds, rec):
            notes[note.note_id] = note
    kyc_sorted = sorted(kyc.values(), key=lambda c: (c.checked_at or "", c.check_id), reverse=True)
    notes_sorted = sorted(notes.values(), key=lambda n: (n.created_at or "", n.note_id))
    identities = verified_identities(ds, kyc_sorted, notes_sorted)
    ids = {r.record_id for r in records}
    merges = tuple(e for e in graph.edges if e.a in ids and e.b in ids)
    recs = sorted(records, key=lambda r: r.record_id)
    return Person(
        records=tuple(recs),
        kyc=tuple(kyc_sorted),
        notes=tuple(notes_sorted),
        identities=identities,
        countries=country_claims(ds, recs, identities, notes_sorted),
        merges=merges,
    )


def person_for(ds: Dataset, record_id: str) -> Person:
    """The whole person (identity group) a customer record belongs to."""
    graph = identity_graph(ds)
    return build_person(ds, [ds.customers[i] for i in graph.group(record_id) if i in ds.customers])


# ------------------------------------------------------------------ names (P8)

_COMPANY_TYPES = {"company", "organisation", "organization", "entity", "business", "corporate"}


def _is_company(etype: str | None) -> bool:
    return (etype or "").strip().lower() in _COMPANY_TYPES


def _variant_groups(ds: Dataset) -> dict[str, frozenset[str]]:
    cached = getattr(ds, "_variant_groups", None)
    if cached is not None:
        return cached
    groups: dict[str, frozenset[str]] = {}
    for group in ds.name_variants.get("given_names", []) if isinstance(ds.name_variants, dict) else []:
        members = frozenset(strip_accents(str(g)).lower() for g in group)
        for m in members:
            groups[m] = groups.get(m, frozenset()) | members
    _cache(ds, "_variant_groups", groups)
    return groups


def _legal_forms(ds: Dataset) -> frozenset[str]:
    forms = ds.name_variants.get("company_legal_forms", []) if isinstance(ds.name_variants, dict) else []
    return frozenset(strip_accents(str(f)).lower() for f in forms)


def _cache(ds: Dataset, attr: str, value) -> None:
    try:
        object.__setattr__(ds, attr, value)
    except Exception:  # noqa: BLE001 - caching is an optimisation only  # pragma: no cover
        pass


def _edit_distance(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _max_edits(a: str, b: str) -> int:
    shorter = min(len(a), len(b))
    if shorter < 4:
        return 0
    return 1 if shorter <= 6 else 2


def _parts_match(a: str, b: str, groups: dict[str, frozenset[str]]) -> bool:
    if a == b:
        return True
    if b in groups.get(a, ()):
        return True
    limit = _max_edits(a, b)
    return limit > 0 and _edit_distance(a, b, limit) <= limit


def _fallback_agree(pa: tuple[str, ...], pb: tuple[str, ...], groups: dict[str, frozenset[str]]) -> bool:
    """SOP P8 on name parts (used until normalize.names.names_agree lands)."""
    if not pa or not pb:
        return False
    used_a: set[int] = set()
    used_b: set[int] = set()
    matches = 0
    for i, x in enumerate(pa):
        for j, y in enumerate(pb):
            if j not in used_b and _parts_match(x, y, groups):
                used_a.add(i)
                used_b.add(j)
                matches += 1
                break
    # written together or apart: "abdulsalam" ~ "abdul salam" (one part covering both)
    for xs, ys, ua, ub in ((pa, pb, used_a, used_b), (pb, pa, used_b, used_a)):
        for i, x in enumerate(xs):
            if i in ua:
                continue
            for j in range(len(ys) - 1):
                if j in ub or j + 1 in ub:
                    continue
                if x == ys[j] + ys[j + 1]:
                    ua.add(i)
                    ub.update({j, j + 1})
                    matches += 1
                    break
    if matches >= 2:
        return True
    if len(pa) <= len(pb):
        return len(used_a) == len(pa)
    return len(used_b) == len(pb)


def names_fit(ds: Dataset, screened: str | None, candidate: str | None, *, company: bool) -> bool:
    """Does ``candidate`` agree with the screened name (SOP P8)?"""
    forms = _legal_forms(ds) if company else frozenset()
    pa = name_parts(screened, company=company, legal_forms=forms)
    pb = name_parts(candidate, company=company, legal_forms=forms)
    if not pa or not pb:
        return False
    try:
        from .normalize.names import names_agree

        result = names_agree(" ".join(pa), [" ".join(pb)], ds.name_variants)
        if result is None:
            return False
        return bool(getattr(result, "agrees", True))
    except NotImplementedError:
        return _fallback_agree(pa, pb, _variant_groups(ds))


def _record_names(rec: Customer) -> list[str]:
    return [n for n in (rec.full_name, rec.preferred_name, rec.login_name) if n]


# ------------------------------------------------------------------ screened details


@dataclass(frozen=True)
class Screened:
    name: str | None
    company: bool
    dob: PartialDate | None
    country: str | None  # canonical
    cities: tuple[str, ...]  # folded city names (alternatives)

    @property
    def described(self) -> str:
        bits = ["name"]
        if self.dob is not None:
            bits.append("date of birth")
        if self.country:
            bits.append("country")
        if self.cities:
            bits.append("city")
        return ", ".join(bits[:-1]) + (" and " + bits[-1] if len(bits) > 1 else bits[0])


_CITY_EXONYMS = {
    "munchen": "munich", "koln": "cologne", "nurnberg": "nuremberg", "wien": "vienna",
    "milano": "milan", "roma": "rome", "napoli": "naples", "torino": "turin",
    "firenze": "florence", "venezia": "venice", "genova": "genoa", "lisboa": "lisbon",
    "praha": "prague", "warszawa": "warsaw", "krakow": "cracow", "bruxelles": "brussels",
    "brussel": "brussels", "geneve": "geneva", "kobenhavn": "copenhagen",
    "goteborg": "gothenburg", "sevilla": "seville", "den haag": "the hague",
    "moskva": "moscow", "athina": "athens", "bucuresti": "bucharest",
}


def _fold(text: str | None) -> str:
    text = text or ""
    if not text.isascii():
        text = strip_accents(text)
    text = text.lower()
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", text)).strip()


def _screened_cities(raw: str | None) -> tuple[str, ...]:
    out: list[str] = []
    for alt in re.split(r"\s*/\s*|\s*;\s*", raw or ""):
        city = _fold(alt.split(",")[0])
        if city and city not in out:
            out.append(city)
    return tuple(out)


def _screened(alert: Alert) -> Screened:
    return Screened(
        name=alert.customer_name,
        company=_is_company(alert.entity_type),
        dob=parse_dob(alert.dob),
        country=canonical_country(alert.country) if alert.country else None,
        cities=_screened_cities(alert.city),
    )


def _city_in(city: str, haystack: str) -> bool:
    if not city or not haystack:
        return False
    if re.search(rf"(?<![0-9a-z]){re.escape(city)}(?![0-9a-z])", haystack):
        return True
    alias = _CITY_EXONYMS.get(city)
    if alias is None:
        for k, v in _CITY_EXONYMS.items():
            if v == city:
                alias = k
                break
    return bool(alias and re.search(rf"(?<![0-9a-z]){re.escape(alias)}(?![0-9a-z])", haystack))


def _record_facts(ds: Dataset, rec: Customer) -> tuple[PartialDate | None, frozenset[str], str]:
    """(parsed DOB, canonical residence/nationality countries, folded city+address), cached."""
    cache = getattr(ds, "_record_facts_cache", None)
    if cache is None:
        cache = {}
        _cache(ds, "_record_facts_cache", cache)
    facts = cache.get(rec.record_id)
    if facts is None:
        countries = frozenset(
            c for c in (canonical_country(v) for v in (rec.residence_country, *rec.nationality) if v) if c
        )
        hay = " | ".join(_fold(v) for v in (rec.city, rec.address) if v)
        facts = (parse_dob(rec.dob), countries, hay)
        cache[rec.record_id] = facts
    return facts


def _fit_reasons(ds: Dataset, s: Screened, rec: Customer, *, check_name: bool = True) -> list[str]:
    """Which screened details the record contradicts ([] = it fits)."""
    misses: list[str] = []
    rec_dob, rec_countries, hay = _record_facts(ds, rec)
    if s.dob is not None:
        if rec_dob is not None and not dates_compatible(s.dob, rec_dob):
            misses.append("date of birth")
    if s.country:
        if rec_countries and s.country not in rec_countries:
            misses.append("country")
    if s.cities:
        if hay and not any(_city_in(c, hay) for c in s.cities):
            misses.append("city")
    if check_name and not misses:
        company = s.company or _is_company(rec.entity_type)
        if name_parts(s.name, company=company, legal_forms=_legal_forms(ds) if company else frozenset()):
            if not any(names_fit(ds, s.name, n, company=company) for n in _record_names(rec)):
                misses.append("name")
    return misses


def record_fits(ds: Dataset, alert: Alert, rec: Customer) -> bool:
    """SOP P2: does this customer record fit every screened detail that is present?"""
    return not _fit_reasons(ds, _screened(alert), rec)


# ------------------------------------------------------------------ name-part index


_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")


def _index_parts(name: str) -> set[str]:
    text = name if name.isascii() else strip_accents(name)
    text = _BRACKETS.sub(" ", text.lower()).replace("'", "").replace("’", "")
    return {t for t in re.split(r"[^0-9a-z]+", text)
            if len(t) >= 2 and not t.isdigit() and t not in PLACEHOLDERS}


class _NameIndex:
    """name part -> record ids, plus a deletion neighbourhood for typo lookups."""

    def __init__(self, ds: Dataset) -> None:
        # A superset of name_parts() (legal forms kept): candidates are verified with
        # names_fit() afterwards, so over-inclusion only costs time.
        by_part: dict[str, set[str]] = {}
        for rid in sorted(ds.customers):
            for name in _record_names(ds.customers[rid]):
                for part in _index_parts(name):
                    by_part.setdefault(part, set()).add(rid)
        self.by_part = by_part
        deletes: dict[str, set[str]] = {}
        for part in sorted(by_part):
            for d in self._deletes(part):
                deletes.setdefault(d, set()).add(part)
        self.deletes = deletes
        self.groups = _variant_groups(ds)

    @staticmethod
    def _deletes(word: str) -> set[str]:
        if len(word) < 4:
            return {word}
        depth = 1 if len(word) <= 6 else 2
        out = {word}
        frontier = {word}
        for _ in range(depth):
            nxt = set()
            for w in frontier:
                for i in range(len(w)):
                    nxt.add(w[:i] + w[i + 1:])
            out |= nxt
            frontier = nxt
        return out

    def similar_parts(self, part: str) -> set[str]:
        found = {part} if part in self.by_part else set()
        for v in self.groups.get(part, ()):
            if v in self.by_part:
                found.add(v)
        for d in self._deletes(part):
            for cand in self.deletes.get(d, ()):
                if _parts_match(part, cand, self.groups):
                    found.add(cand)
        return found

    def candidates(self, parts: tuple[str, ...]) -> list[str]:
        vocab: set[str] = set()
        for p in parts:
            vocab |= self.similar_parts(p)
        for i in range(len(parts) - 1):  # written apart in the screened name
            joined = parts[i] + parts[i + 1]
            if joined in self.by_part:
                vocab.add(joined)
        for p in parts:  # written together in the screened name
            for k in range(2, len(p) - 1):
                if p[:k] in self.by_part and p[k:] in self.by_part:
                    vocab.update({p[:k], p[k:]})
        ids: set[str] = set()
        for v in vocab:
            ids |= self.by_part.get(v, set())
        return sorted(ids)


def _name_index(ds: Dataset) -> _NameIndex:
    idx = getattr(ds, "_name_part_index", None)
    if idx is None:
        idx = _NameIndex(ds)
        _cache(ds, "_name_part_index", idx)
    return idx


def search_customers(ds: Dataset, alert: Alert) -> list[str]:
    """SOP P2 without a link: record ids that fit every screened detail present."""
    s = _screened(alert)
    forms = _legal_forms(ds) if s.company else frozenset()
    parts = name_parts(s.name, company=s.company, legal_forms=forms)
    if not parts:
        return []  # without a name there is nothing to search on safely
    out = []
    for rid in _name_index(ds).candidates(parts):
        if not _fit_reasons(ds, s, ds.customers[rid]):
            out.append(rid)
    return out


# ------------------------------------------------------------------ link following


def _linked_customers(ds: Dataset, alert: Alert) -> tuple[list[str], str, str, list[str]]:
    """(record ids, method, link description, account ids) from the alert's link."""
    if alert.account_ref:
        accounts = ds.accounts_by_id.get(alert.account_ref, [])
        ids = sorted({a.customer_id for a in accounts if a.customer_id in ds.customers})
        return ids, "account_ref", f"account_ref {alert.account_ref}", [alert.account_ref]
    if alert.login_handle:
        accounts = ds.accounts_by_login.get(alert.login_handle.strip().lower(), [])
        ids = sorted({a.customer_id for a in accounts if a.customer_id in ds.customers})
        accs = sorted({a.account_id for a in accounts})
        return ids, "login_handle", f"login_handle {alert.login_handle}", accs
    if alert.email:
        found = ds.customers_by_email.get(alert.email.strip().lower(), [])
        ids = sorted({c.record_id for c in found})
        return ids, "email", f"email {alert.email}", []
    return [], "unlinked", "", []


def _link_step(method: str, link: str, ids: list[str], accs: list[str], kept: list[str],
               s: Screened) -> str:
    if not ids:
        return f"{link} -> no customer record"
    if len(ids) == 1:
        if method == "account_ref":
            return f"{link} -> accounts -> customer {ids[0]}"
        if method == "login_handle":
            return f"{link} -> accounts {', '.join(accs)} -> customer {ids[0]}"
        return f"{link} -> customers -> {ids[0]}"
    holders = ", ".join(ids[:-1]) + " and " + ids[-1]
    what = "accounts: joint holders" if method == "account_ref" else "customers:"
    if kept and len(kept) < len(ids):
        return f"{link} -> {what} {holders} -> the screened {s.described} fit {', '.join(kept)}"
    if kept:
        return f"{link} -> {what} {holders} -> the screened {s.described} fit all of them"
    return f"{link} -> {what} {holders} -> the screened {s.described} fit none of them"


def _group_persons(ds: Dataset, record_ids: list[str]) -> list[Person]:
    graph = identity_graph(ds)
    groups: dict[str, tuple[str, ...]] = {}
    for rid in sorted(record_ids):
        members = graph.group(rid)
        groups[members[0]] = members
    return [build_person(ds, [ds.customers[i] for i in groups[k] if i in ds.customers])
            for k in sorted(groups)]


def _person_steps(ds: Dataset, persons: list[Person], matched: list[str]) -> list[str]:
    steps: list[str] = []
    if len(persons) > 1:
        ids = ", ".join(r for p in persons for r in p.record_ids)
        steps.append(f"{len(persons)} different people fit (records {ids}); each is decided separately")
    graph = identity_graph(ds)
    for p in persons:
        if len(p.records) > 1:
            steps.append("same phone and date of birth, or same document number on approved checks "
                         f"-> one person: {', '.join(p.record_ids)}")
        v = p.verified
        if v is None:
            continue
        if v.kind == "kyc_check":
            for rec in p.record_ids:
                for acc, app in graph.applicants_by_customer.get(rec, ()):
                    if any(c.check_id == v.source_id and c.applicant_id == app for c in p.kyc):
                        steps.append(f"{rec} -> accounts {acc} -> onboarding {app} "
                                     f"-> latest approved check {v.source_id}")
                        break
                else:
                    continue
                break
        else:
            steps.append(f"customer email -> notes -> {v.source_id}")
    return steps


def _resolution_cache(ds: Dataset) -> dict[str, tuple[Alert, Resolution]]:
    cache = getattr(ds, "_resolution_cache", None)
    if cache is None:
        cache = {}
        _cache(ds, "_resolution_cache", cache)
    return cache


def resolve_customer(ds: Dataset, alert: Alert) -> Resolution:
    cache = _resolution_cache(ds)
    key = alert.alert_id
    if key and key in cache and cache[key][0] is alert:
        return cache[key][1]
    res = _resolve(ds, alert)
    if key:
        cache[key] = (alert, res)
    return res


def _resolve(ds: Dataset, alert: Alert) -> Resolution:
    s = _screened(alert)
    ids, method, link, accs = _linked_customers(ds, alert)
    path: list[str] = []
    if method != "unlinked" and ids:
        kept = ids
        if len(ids) > 1:  # a link to several people: the screened details pick (P2)
            kept = [i for i in ids if not _fit_reasons(ds, s, ds.customers[i])]
            path.append(_link_step(method, link, ids, accs, kept, s))
            if not kept:  # nobody the link leads to fits: nobody is found (see docstring)
                return Resolution(persons=(), method="none", path=tuple(path), candidates=tuple(ids))
        else:
            path.append(_link_step(method, link, ids, accs, ids, s))
        persons = _group_persons(ds, kept)
        path.extend(_person_steps(ds, persons, kept))
        return Resolution(persons=tuple(persons), method=method, path=tuple(path),
                          candidates=tuple(ids))
    if method != "unlinked":
        path.append(_link_step(method, link, ids, accs, [], s))
        path.append("the link leads to no customer record; searching by the screened details")
    found = search_customers(ds, alert)
    path.append(f"{s.described} -> customers" + ("" if found else " -> no record fits"))
    if not found:
        return Resolution(persons=(), method="none", path=tuple(path))
    persons = _group_persons(ds, found)
    path.extend(_person_steps(ds, persons, found))
    return Resolution(persons=tuple(persons), method="unlinked", path=tuple(path),
                      candidates=tuple(found))
