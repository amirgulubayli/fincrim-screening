"""P6 Name / P8 agreement rule, on tiny hand-built records."""

from dataclasses import dataclass
from pathlib import Path

from screening.contracts import Alert, Customer, Facet, Hit, KycCheck, ReasonCode, Signal
from screening.facts import HitFacts
from screening.loaders import Dataset
from screening.resolve import Person
from screening.rules import name
from screening.rules.base import Context

VARIANTS = {"groups": [["Mohammed", "Muhammad", "Mohd"]], "legal_forms": ["Ltd", "Limited", "AB"]}


@dataclass(frozen=True)
class _Verified:
    source_id: str
    name: str | None


@dataclass(frozen=True)
class NewPerson:
    """Stand-in for the P4 Person (verified identity from KYC or an onboarding note)."""

    records: tuple
    verified: _Verified | None = None

    @property
    def verified_name(self):
        return self.verified.name if self.verified else None

    @property
    def verified_source_id(self):
        return self.verified.source_id if self.verified else None

    @property
    def record_ids(self):
        return tuple(sorted(r.record_id for r in self.records))


def ctx(hit_names, person=None, screened="Someone Else", company=False):
    names = tuple(hit_names)
    etype = "Company" if company else "Individual"
    hit = Hit(hit_id="H-1", name=names[0] if names else None, aliases=names[1:], entity_type=etype)
    facts = HitFacts(
        hit_id="H-1",
        names=names,
        dobs_raw=(),
        dob_sources=(),
        countries_raw=(),
        deceased_date=None,
        listing_phrase="",
        search_results=(),
        entity_type=etype,
        is_company=company,
    )
    alert = Alert(alert_id="A-1", customer_name=screened, entity_type=etype)
    ds = Dataset(root=Path("."), name_variants=VARIANTS)
    return Context(ds=ds, alert=alert, hit=hit, facts=facts, person=person)


def cust(rid="CUS-1", full=None, preferred=None, etype="Individual"):
    return Customer(record_id=rid, full_name=full, preferred_name=preferred, entity_type=etype)


def verified_person(vname, *records, source="KYC-1"):
    return NewPerson(records=records or (cust(full="Record Name"),), verified=_Verified(source, vname))


def one(results):
    assert len(results) == 1
    r = results[0]
    assert r.facet is Facet.NAME and r.rule_id == "P6.name"
    return r


# ------------------------------------------------------------------ contradiction


def test_verified_name_agreeing_with_no_hit_name_is_mismatch():
    p = verified_person("Chinedu Azeez", cust(full="Ridwan Azeez"))
    r = one(name.check(ctx(["Ridwan, Azeez", "Ridwan Ade"], p)))
    assert r.signal is Signal.CONTRADICTION
    assert r.reason is ReasonCode.NAME_MISMATCH_VERIFIED
    assert r.evidence == ("KYC-1",)
    assert "Chinedu Azeez (KYC-1)" in r.summary and "none of the hit's names" in r.summary


def test_note_verified_name_counts():
    p = verified_person("Jonathan Reyes Whitfield", source="NOTE-9")
    r = one(name.check(ctx(["John Whitfield"], p)))
    assert r.signal is Signal.CONTRADICTION and r.evidence == ("NOTE-9",)


def test_any_alias_agreeing_prevents_mismatch():
    p = verified_person("Maria Lopez Garcia")
    r = one(name.check(ctx(["Pedro Sanchez", "Maria Lopez"], p)))
    assert r.signal is Signal.AGREEMENT
    assert "Maria Lopez" in r.summary and r.evidence == ("KYC-1",)


def test_record_name_mismatch_never_contradicts():
    p = NewPerson(records=(cust(full="Peter Smith", preferred="Pete Smith"),))
    r = one(name.check(ctx(["Ivan Petrov"], p)))
    assert r.signal is Signal.NEUTRAL and r.reason is None


def test_screened_name_mismatch_never_contradicts():
    r = one(name.check(ctx(["Ivan Petrov"], None, screened="Peter Smith")))
    assert r.signal is Signal.NEUTRAL


def test_placeholder_verified_name_cannot_judge():
    p = verified_person("TEST NA")
    r = one(name.check(ctx(["Ivan Petrov"], p)))
    assert r.signal is Signal.NEUTRAL and "neither confirms nor clears" in r.summary


def test_single_part_verified_name_cannot_judge():
    p = verified_person("Madonna", cust(full="XXX"))
    r = one(name.check(ctx(["Ivan Petrov"], p)))
    assert r.signal is Signal.NEUTRAL
    assert "fewer than two real name parts" in r.summary


def test_no_hit_names_cannot_judge():
    p = verified_person("Chinedu Azeez")
    assert one(name.check(ctx([], p))).signal is Signal.NEUTRAL


def test_verified_identity_without_name_does_not_contradict():
    p = verified_person(None, cust(full="Peter Smith"))
    assert one(name.check(ctx(["Ivan Petrov"], p))).signal is Signal.NEUTRAL


# ------------------------------------------------------------------ agreement


def test_verified_name_agreement_beats_record_name():
    p = verified_person("Elvira Velasco Morillo", cust(full="Elvi V"))
    r = one(name.check(ctx(["VELASCO MORILLO, Elvira"], p)))
    assert r.signal is Signal.AGREEMENT and not r.weak
    assert r.summary.startswith("The customer's verified legal name Elvira Velasco Morillo (KYC-1)")


def test_fuzzy_agreement_is_weak():
    p = verified_person("Jonathon Whitfeld")
    r = one(name.check(ctx(["Jonathan Whitfield"], p)))
    assert r.signal is Signal.AGREEMENT and r.weak and r.weak_why == "NAME_FUZZY_MATCH"


def test_variant_agreement_is_not_weak():
    p = verified_person("Mohd Al Rashid")
    r = one(name.check(ctx(["Muhammad Al-Rashid"], p)))
    assert r.signal is Signal.AGREEMENT and not r.weak


def test_record_name_agreement_without_verified_name_cites_record():
    p = NewPerson(records=(cust("CUS-2", full="Ivan Petrov"), cust("CUS-1", full="Johnny X")))
    r = one(name.check(ctx(["Petrov, Ivan"], p)))
    assert r.signal is Signal.AGREEMENT and r.evidence == ("CUS-2",)
    assert r.summary == "The customer's name agrees with the hit's name Petrov, Ivan."


def test_preferred_name_agreement():
    p = NewPerson(records=(cust(full="TEST", preferred="Elvira Velasco"),))
    r = one(name.check(ctx(["Elvira Velasco Morillo"], p)))
    assert r.signal is Signal.AGREEMENT and r.evidence == ("CUS-1",)


def test_unjudgeable_verified_falls_back_to_record_agreement():
    p = verified_person("NA", cust(full="Ivan Petrov"))
    r = one(name.check(ctx(["Ivan Petrov"], p)))
    assert r.signal is Signal.AGREEMENT and r.evidence == ("CUS-1",)


def test_screened_name_used_only_without_person():
    r = one(name.check(ctx(["Ivan Petrov"], None, screened="Ivan Petrov")))
    assert r.signal is Signal.AGREEMENT and r.weak and r.weak_why == "NAME_FROM_SCREENED_ONLY"
    # with a resolved person the screened name is ignored
    p = NewPerson(records=(cust(full="Peter Smith"),))
    assert one(name.check(ctx(["Ivan Petrov"], p, screened="Ivan Petrov"))).signal is Signal.NEUTRAL


def test_hit_single_part_alias_can_agree():
    p = verified_person("Ivan Petrov")
    r = one(name.check(ctx(["Someone Different", "Petrov"], p)))
    assert r.signal is Signal.AGREEMENT


def test_company_legal_forms_are_not_parts():
    p = NewPerson(records=(cust(full="Acme Trading Ltd", etype="Company"),))
    r = one(name.check(ctx(["ACME TRADING LIMITED"], p, company=True)))
    assert r.signal is Signal.AGREEMENT
    q = verified_person("Nordic Holding AB", cust(etype="Company"))
    r2 = one(name.check(ctx(["Baltic Shipping AB"], q, company=True)))
    assert r2.signal is Signal.CONTRADICTION


# ------------------------------------------------------------------ old Person / robustness


def test_verified_kyc_name_beats_record_name():
    from screening.identity import VerifiedIdentity

    kyc = KycCheck(check_id="KYC-7", verified_name="Chinedu Azeez", result="approved")
    vid = VerifiedIdentity(source_id="KYC-7", kind="kyc_check", checked_at=None, name="Chinedu Azeez")
    p = Person(records=(cust(full="Ridwan Azeez"),), kyc=(kyc,), identities=(vid,))
    r = one(name.check(ctx(["Ridwan Azeez"], p)))
    assert r.signal is Signal.CONTRADICTION and r.evidence == ("KYC-7",)


def test_deterministic_and_never_raises():
    p = verified_person("Chinedu Azeez")
    c = ctx(["Ridwan, Azeez"], p)
    assert name.check(c) == name.check(c)
    broken = Context(ds=None, alert=None, hit=None, facts=None, person=None)
    assert name.check(broken) == []
