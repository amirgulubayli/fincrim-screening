"""Customer resolution (SOP P2-P4) on tiny hand-built datasets (invented values only)."""

from pathlib import Path

from screening.contracts import Account, Alert, Customer, KycCheck, Note, Onboarding
from screening.identity import parse_dob
from screening.loaders import Dataset
from screening.normalize.dates import PartialDate
from screening.resolve import (
    _fallback_agree,
    names_fit,
    person_for,
    record_fits,
    resolve_customer,
    search_customers,
)

VARIANTS = {
    "given_names": [["muhammad", "mohammed", "mohammad"], ["joseph", "josef", "joe"]],
    "company_legal_forms": ["ltd", "gmbh", "ag", "se", "sa"],
}


def cus(rid, name, dob=None, nat=(), res=None, city=None, address=None, email=None, phone=None,
        etype="Individual", preferred=None):
    return Customer(record_id=rid, entity_type=etype, full_name=name, preferred_name=preferred,
                    dob=dob, nationality=tuple(nat), residence_country=res, city=city,
                    address=address, email=email, phone=phone)


def make_ds(customers, accounts=(), onboarding=(), kyc=(), notes=(), alerts=()):
    ds = Dataset(root=Path("."))
    ds.customers = {c.record_id: c for c in customers}
    ds.accounts = list(accounts)
    ds.onboarding = list(onboarding)
    ds.kyc_checks = {k.check_id: k for k in kyc}
    ds.notes = {n.note_id: n for n in notes}
    ds.alerts = {a.alert_id: a for a in alerts}
    ds.name_variants = VARIANTS
    ds.build_indexes()
    return ds


def alert(aid="A-1", name="Ana Exampleton", dob=None, country=None, city=None, etype="Individual", **link):
    return Alert(alert_id=aid, customer_name=name, entity_type=etype, dob=dob, country=country,
                 city=city, **link)


# ----------------------------------------------------------------- P2: joint accounts


def joint_ds():
    return make_ds(
        [cus("C-1", "Ana Exampleton", "1980-03-05", ["Spain"], "ES", "Madrid"),
         cus("C-2", "Bruno Exampleton", "1978-11-30", ["Spain"], "ES", "Madrid")],
        accounts=[Account(account_id="ACC-J", customer_id="C-1"),
                  Account(account_id="ACC-J", customer_id="C-2")],
    )


def test_joint_account_screened_name_and_dob_pick_the_holder():
    ds = joint_ds()
    res = resolve_customer(ds, alert(name="Ana Exampleton", dob="1980-03-05", account_ref="ACC-J"))
    assert [p.record_ids for p in res.persons] == [("C-1",)]
    assert res.method == "account_ref"
    assert res.candidates == ("C-1", "C-2")
    assert res.path[0].startswith("account_ref ACC-J -> accounts: joint holders C-1 and C-2 -> the screened")
    assert res.path[0].endswith("fit C-1")


def test_joint_account_dob_alone_can_pick():
    ds = joint_ds()
    # both share the surname; "Exampleton" alone agrees with both, the DOB decides
    res = resolve_customer(ds, alert(name="Exampleton", dob="1978", account_ref="ACC-J"))
    assert [p.key for p in res.persons] == ["C-2"]


def test_joint_account_without_distinguishing_details_keeps_both():
    ds = joint_ds()
    res = resolve_customer(ds, alert(name="Exampleton", account_ref="ACC-J"))
    assert [p.key for p in res.persons] == ["C-1", "C-2"]
    assert res.ambiguous


def test_joint_account_nobody_fits_resolves_to_nobody():
    ds = joint_ds()
    res = resolve_customer(ds, alert(name="Ana Exampleton", dob="1990-01-01", account_ref="ACC-J"))
    assert res.persons == ()
    assert res.method == "none"
    assert "fit none of them" in res.path[0]


def test_single_holder_link_is_followed_as_is():
    ds = joint_ds()
    ds.accounts.append(Account(account_id="ACC-S", customer_id="C-1", login_handle="anae1"))
    ds.build_indexes()
    res = resolve_customer(ds, alert(name="Somebody Else", dob="1999", login_handle="ANAE1"))
    assert [p.key for p in res.persons] == ["C-1"]
    assert res.path[0] == "login_handle ANAE1 -> accounts ACC-S -> customer C-1"


# ----------------------------------------------------------------- P2: unlinked search


def search_ds():
    return make_ds([
        cus("C-10", "Mohammad Karim Testwala", "February 1968", ["British"], "United Kingdom",
            "Leeds", "1 Mill Lane, Leeds"),
        cus("C-11", "Mohammad Karim Testwala", "12/03/1968", ["British"], "GB", "Leeds"),
        cus("C-12", "Mohammad Karim Testwala", "1974-07-10", ["British"], "GB", "Leeds"),
        cus("C-13", "Mohammad Karim Testwala", "1968-02-14", ["Pakistani"], "PK", "Lahore"),
        cus("C-14", "Mohammad Karim Testwala", "1968-02-20", ["British"], "GB", "Cardiff",
            "2 Oak Drive, Cardiff"),
        cus("C-15", "Unrelated Person", "1968-02-14", ["British"], "GB", "Leeds"),
    ])


def test_unlinked_search_uses_every_screened_detail():
    ds = search_ds()
    # "Muhammad" is a listed variant; DOB is year+month; country by name vs code/demonym
    a = alert(name="Muhammad Karim TESTWALA", dob="1968-02", country="United Kingdom", city="Leeds")
    assert search_customers(ds, a) == ["C-10"]
    res = resolve_customer(ds, a)
    assert res.method == "unlinked"
    assert [p.key for p in res.persons] == ["C-10"]
    assert res.path[0] == "name, date of birth, country and city -> customers"


def test_unlinked_search_without_dob_leans_on_name_country_city():
    ds = search_ds()
    a = alert(name="Mohammad Karim Testwala", country="United Kingdom", city="Cardiff")
    assert search_customers(ds, a) == ["C-14"]


def test_unlinked_search_city_in_address_and_typo_in_name():
    ds = make_ds([cus("C-20", "Hannah Brightwater Lee", "1998-08-08", ["American"], "US", None,
                      "13 Oak Drive, Seattle")])
    a = alert(name="Hanah Brightwater Lee", dob="1998-08-08", country="United States", city="Seattle, WA")
    assert search_customers(ds, a) == ["C-20"]


def test_unlinked_two_people_fit_is_ambiguous():
    ds = search_ds()
    a = alert(name="Mohammad Karim Testwala", dob="1968", country="United Kingdom", city="Leeds")
    res = resolve_customer(ds, a)
    assert [p.key for p in res.persons] == ["C-10", "C-11"]
    assert res.ambiguous
    assert res.path[1].startswith("2 different people fit (records C-10, C-11)")


def test_unlinked_nobody_fits_is_none():
    ds = search_ds()
    res = resolve_customer(ds, alert(name="Mohammad Karim Testwala", dob="1990"))
    assert res.persons == () and res.method == "none"


def test_missing_record_facts_never_exclude():
    rec = cus("C-30", "Ana Exampleton")
    ds = make_ds([rec])
    assert record_fits(ds, alert(dob="1980-01-02", country="Spain", city="Madrid"), rec)


def test_company_legal_forms_are_not_name_parts():
    ds = make_ds([cus("C-40", "Northwind Bank AG", "2013-02", ["Germany"], "Germany", "Berlin",
                      etype="Company")])
    a = alert(name="Northwind Bank SE", dob="2013-02", country="Germany", city="Berlin", etype="Company")
    assert search_customers(ds, a) == ["C-40"]


# ----------------------------------------------------------------- P8 fallback matcher


def test_fallback_name_agreement_rules():
    groups = {"joseph": frozenset({"joseph", "joe"}), "joe": frozenset({"joseph", "joe"})}
    assert _fallback_agree(("abdulsalam", "testor"), ("abdul", "salam", "testor"), {})  # together/apart
    assert _fallback_agree(("marcus", "fenwick"), ("markus", "fenwick"), {})  # 1 edit on 6 letters
    assert not _fallback_agree(("ana", "lee"), ("ann", "lee"), {})  # <4 letters must be identical
    assert _fallback_agree(("joe", "fairbanks"), ("joseph", "fairbanks"), groups)
    assert not _fallback_agree(("ikenna", "okorafor"), ("tunde", "okorafor"), {})  # one match is not enough
    assert _fallback_agree(("okorafor",), ("tunde", "okorafor"), {})  # every part of the shorter name


def test_names_fit_ignores_placeholders():
    ds = make_ds([])
    assert not names_fit(ds, "XXX 0000", "Ana Exampleton", company=False)


# ----------------------------------------------------------------- P3 identity graph in resolution


def graph_ds():
    return make_ds(
        [cus("C-50", "Ana Exampleton", "1980-03-05", ["Spain"], "ES", email="a@x.test", phone="+34 600 111"),
         cus("C-51", "Ana Exampleton", "5 March 1980", ["Peruvian"], "Peru", email="b@x.test",
             phone="+34600111"),
         cus("C-52", "Pia Exampleton", "1982-01-01", ["Spain"], "ES", phone="+34 600 111")],
        accounts=[Account(account_id="ACC-50", customer_id="C-50")],
    )


def test_resolution_expands_to_the_whole_person():
    ds = graph_ds()
    res = resolve_customer(ds, alert(account_ref="ACC-50"))
    assert [p.record_ids for p in res.persons] == [("C-50", "C-51")]  # C-52 shares only the phone
    person = res.persons[0]
    assert person.key == "C-50"
    assert [(m.a, m.b, m.why) for m in person.merges] == [("C-50", "C-51", "phone+dob")]
    assert any(step.endswith("-> one person: C-50, C-51") for step in res.path)
    assert {c.canonical for c in person.countries} == {"Spain", "Peru"}


# ----------------------------------------------------------------- P4 verified identity


def verified_ds():
    return make_ds(
        [cus("C-60", "Joe Exampleton", "1981-01-01", ["Ghanaian"], "Netherlands", email="joe@x.test")],
        accounts=[Account(account_id="ACC-60", customer_id="C-60")],
        onboarding=[Onboarding(account_id="ACC-60", customer_id="C-60", kyc_applicant_id="APP-60")],
        kyc=[
            KycCheck(check_id="KYC-1", applicant_id="APP-60", verified_name="Joseph Exampleton",
                     verified_dob="02/01/1981", document_country="Ghana", document_number="G1",
                     result="approved", checked_at="2023-01-10"),
            KycCheck(check_id="KYC-2", applicant_id="APP-60", verified_name="Wrong Person",
                     verified_dob="1990-01-01", document_country="Chad", result="rejected",
                     checked_at="2025-01-10"),
            KycCheck(check_id="KYC-3", applicant_id="APP-60", verified_name="Old Person",
                     verified_dob="1991-01-01", document_country="Togo", result="expired",
                     checked_at="2025-02-10"),
        ],
        notes=[
            Note(note_id="NOTE-1", requester_email="JOE@x.test", created_at="2024-05-01", channel="kyc",
                 text="Onfido confirmed a Ghanaian passport for JOSEPH KWAME EXAMPLETON, DOB 2 January 1981, "
                      "on 2024-05-01."),
            Note(note_id="NOTE-2", requester_email="joe@x.test", created_at="2024-06-01", channel="phone",
                 text="Between 2010 and 2015 the client lived and worked in Portugal."),
            Note(note_id="NOTE-3", requester_email="joe@x.test", created_at="2024-07-01", channel="app",
                 text="Customer asked why a card payment was declined at a supermarket."),
        ],
    )


def test_verified_identity_is_the_newest_approved_check_kyc_or_note():
    ds = verified_ds()
    person = resolve_customer(ds, alert(name="Joe Exampleton", account_ref="ACC-60")).persons[0]
    assert [k.check_id for k in person.kyc] == ["KYC-1"]  # rejected / expired are not evidence
    assert person.verified_source_id == "NOTE-1"
    assert person.verified_name == "JOSEPH KWAME EXAMPLETON"
    assert person.verified_dob == "2 January 1981"
    assert person.verified_dob_date == PartialDate(1981, 1, 2)
    assert person.verified_document_country == "Ghanaian"
    assert [v.source_id for v in person.identities] == ["NOTE-1", "KYC-1"]
    assert [v.source_id for v in person.kyc_notes] == ["NOTE-1"]
    res = resolve_customer(ds, alert(aid="A-2", name="Joe Exampleton", account_ref="ACC-60"))
    assert "customer email -> notes -> NOTE-1" in res.path


def test_verified_identity_from_kyc_when_it_is_newest():
    ds = verified_ds()
    ds.notes = {}
    ds.build_indexes()
    res = resolve_customer(ds, alert(aid="A-3", account_ref="ACC-60"))
    person = res.persons[0]
    assert person.verified_source_id == "KYC-1"
    assert person.verified_dob_date == PartialDate(1981, 1, 2)  # slash dates are day-first
    assert "C-60 -> accounts ACC-60 -> onboarding APP-60 -> latest approved check KYC-1" in res.path


def test_person_countries_cover_records_documents_and_notes():
    ds = verified_ds()
    person = person_for(ds, "C-60")
    kinds = {(c.kind, c.raw, c.source_id) for c in person.countries}
    assert ("residence", "Netherlands", "C-60") in kinds
    assert ("nationality", "Ghanaian", "C-60") in kinds
    assert ("document", "Ghana", "KYC-1") in kinds
    assert ("document", "Ghanaian", "NOTE-1") in kinds
    assert ("note_residence", "Portugal", "NOTE-2") in kinds
    assert [(c.raw, c.source_id) for c in person.note_countries] == [("Portugal", "NOTE-2")]
    assert "Togo" not in person.country_values and "Chad" not in person.country_values
    assert {c.canonical for c in person.countries} == {"Netherlands", "Ghana", "Portugal"}


def test_resolution_is_deterministic_and_cached():
    ds = graph_ds()
    a = alert(account_ref="ACC-50")
    assert resolve_customer(ds, a) is resolve_customer(ds, a)
    fresh = graph_ds()
    assert resolve_customer(fresh, a) == resolve_customer(ds, a)


def test_parse_dob_reads_slash_dates_day_first():
    assert parse_dob("08/02/1985") == PartialDate(1985, 2, 8)
    assert parse_dob("31/12/1999") == PartialDate(1999, 12, 31)
    assert parse_dob("1990") == PartialDate(1990)
    assert parse_dob("not a date") is None
