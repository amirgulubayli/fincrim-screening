"""Identity graph (SOP P3) and verified identity from notes (SOP P4). Invented values only."""

from pathlib import Path

import pytest

from screening.contracts import Account, Customer, KycCheck, Onboarding
from screening.identity import (
    canonical_country,
    extract_residence_countries,
    identity_graph,
    parse_kyc_note,
)
from screening.loaders import Dataset
from screening.normalize.dates import PartialDate


def cus(rid, dob=None, phone=None):
    return Customer(record_id=rid, entity_type="Individual", full_name=f"Person {rid}", dob=dob, phone=phone)


def make_ds(customers, accounts=(), onboarding=(), kyc=()):
    ds = Dataset(root=Path("."))
    ds.customers = {c.record_id: c for c in customers}
    ds.accounts = list(accounts)
    ds.onboarding = list(onboarding)
    ds.kyc_checks = {k.check_id: k for k in kyc}
    ds.build_indexes()
    return ds


# ----------------------------------------------------------------- graph


def test_phone_and_full_dob_merge_across_date_formats():
    ds = make_ds([cus("C-1", "1975-06-07", "+44 7700 900123"), cus("C-2", "07/06/1975", "+447700900123"),
                  cus("C-3", "June 7, 1975", "44-7700-900123")])
    g = identity_graph(ds)
    assert g.group("C-2") == ("C-1", "C-2", "C-3")
    assert g.group_key("C-3") == "C-1"


def test_shared_phone_alone_or_partial_dob_does_not_merge():
    ds = make_ds([cus("C-1", "1975-06-07", "+44 1"), cus("C-2", "1980-01-01", "+44 1"),  # family
                  cus("C-3", "1975", "+44 555 0101"), cus("C-4", "1975", "+44 555 0101"),  # year only
                  cus("C-5", "1975-06-07", None), cus("C-6", "1975-06-07", None)])  # DOB alone
    g = identity_graph(ds)
    for rid in ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6"):
        assert g.group(rid) == (rid,)


def doc_ds(result_b="approved"):
    customers = [cus("C-1", "1970-01-01", "+1 111111"), cus("C-2", "1971-02-02", "+1 222222"),
                 cus("C-3", "1972-03-03", "+1 333333"), cus("C-4", "1972-03-03", "+1 333333")]
    accounts = [Account(account_id=f"ACC-{i}", customer_id=f"C-{i}") for i in (1, 2, 3)]
    onboarding = [Onboarding(account_id=f"ACC-{i}", customer_id=f"C-{i}", kyc_applicant_id=f"APP-{i}")
                  for i in (1, 2, 3)]
    kyc = [KycCheck(check_id="KYC-A", applicant_id="APP-1", document_number="P 123 456",
                    result="approved", checked_at="2024-01-01"),
           KycCheck(check_id="KYC-B", applicant_id="APP-2", document_number="p123456",
                    result=result_b, checked_at="2024-02-01"),
           KycCheck(check_id="KYC-C", applicant_id="APP-3", document_number="P123456",
                    result="approved", checked_at="2024-03-01")]
    return make_ds(customers, accounts, onboarding, kyc)


def test_document_number_on_approved_checks_merges_transitively():
    g = identity_graph(doc_ds())
    # C-1/C-2/C-3 share a document number; C-4 joins C-3 via phone + full DOB
    assert g.group("C-4") == ("C-1", "C-2", "C-3", "C-4")
    assert sorted({e.why for e in g.edges}) == ["document", "phone+dob"]


def test_document_number_on_rejected_check_does_not_merge():
    g = identity_graph(doc_ds(result_b="rejected"))
    assert g.group("C-2") == ("C-2",)
    assert g.group("C-1") == ("C-1", "C-3", "C-4")


def test_graph_is_cached_per_dataset():
    ds = doc_ds()
    assert identity_graph(ds) is identity_graph(ds)


# ----------------------------------------------------------------- KYC notes (paraphrased shapes)

KYC_NOTES = [
    ("Identity check done: Veriff processed a passport (Portugal) for LIA MARTA COSTELLO, DOB 1977-04-09, "
     "on 2024-02-03.", "LIA MARTA COSTELLO", PartialDate(1977, 4, 9), "Portugal", "2024-02-03"),
    ("National ID from Kenya verified by Onfido on 2023-05-06 for OTIENO BARASA, DOB 11/12/1961.",
     "OTIENO BARASA", PartialDate(1961, 12, 11), "Kenya", "2023-05-06"),
    ("PIETER VAN DAMME's identity was confirmed via Sumsub on 2025-01-02 using a Dutch passport. "
     "DOB recorded as March 3, 1990.", "PIETER VAN DAMME", PartialDate(1990, 3, 3), "Dutch", "2025-01-02"),
    ("2024-08-09: Persona confirmed driving licence for AMARA OKAFOR, DOB 4 May 1985, ctry Nigeria.",
     "AMARA OKAFOR", PartialDate(1985, 5, 4), "Nigeria", "2024-08-09"),
    ("The passport of TOMAS NOVAK from Czech Republic was checked by Veriff on 2023-03-04, noting a date "
     "of birth of 1969-10-21.", "TOMAS NOVAK", PartialDate(1969, 10, 21), "Czech Republic", "2023-03-04"),
    ("Onfido approved the national ID issued in Austria for KLARA HUBER (02/05/1993) on 2024-04-04.",
     "KLARA HUBER", PartialDate(1993, 5, 2), "Austria", "2024-04-04"),
    ("On 2022-12-12, Sumsub validated the residence permit of RAVI MENON, who was born on 9 July 1979 "
     "in India.", "RAVI MENON", PartialDate(1979, 7, 9), "India", "2022-12-12"),
    ("Verified via Persona on 2025-06-07: passport from United Arab Emirates, holder NOOR HADDAD, date of "
     "birth 1988-01-30.", "NOOR HADDAD", PartialDate(1988, 1, 30), "United Arab Emirates", "2025-06-07"),
    ("SIOBHAN O'NEILL cleared a Veriff identity check on 2024-09-10 with an Irish driving licence, "
     "confirming birth on December 1, 1975.", "SIOBHAN O'NEILL", PartialDate(1975, 12, 1), "Irish",
     "2024-09-10"),
    ("Persona checked ANDRZEJ NOWAK on 2023-11-11; his Poland passport shows 1958-02-17 as the birth date.",
     "ANDRZEJ NOWAK", PartialDate(1958, 2, 17), "Poland", "2023-11-11"),
    ("Identity verification for ZANELE DLAMINI, born 1966, completed through Onfido on 2024-01-15 using "
     "a South African passport.", "ZANELE DLAMINI", PartialDate(1966), "South African", "2024-01-15"),
    ("Sumsub confirmed the identity of ŁUKASZ N/A N/A, DOB September 15, 1984, on 2025-09-12 via a "
     "driving licence from Ireland.", "ŁUKASZ N/A N/A", PartialDate(1984, 9, 15), "Ireland", "2025-09-12"),
]


@pytest.mark.parametrize("text,name,dob,country,checked", KYC_NOTES)
def test_kyc_note_shapes_parse(text, name, dob, country, checked):
    v = parse_kyc_note(text, "2026-01-01", "NOTE-X")
    assert v is not None
    assert (v.name, v.dob, v.document_country, v.checked_at) == (name, dob, country, checked)
    assert v.source_id == "NOTE-X" and v.kind == "note"


def test_kyc_note_without_date_in_text_uses_created_at():
    v = parse_kyc_note("Onfido verified the passport from Ghana for KOFI MENSAH, DOB 1980-01-01.",
                       "2024-03-03T10:00:00", "NOTE-Y")
    assert v.checked_at == "2024-03-03"


@pytest.mark.parametrize("text", [
    "Onfido rejected the passport from Ghana for KOFI MENSAH, DOB 1980-01-01, on 2024-01-01.",
    "Veriff check failed for KOFI MENSAH (DOB 1980-01-01) on 2024-01-01: document unreadable.",
    "The national ID of KOFI MENSAH, born 1980-01-01, had expired when Sumsub reviewed it on 2024-01-01.",
    "Persona could not verify KOFI MENSAH, DOB 1980-01-01, on 2024-01-01.",
    "Customer asked why a card payment was declined at a supermarket. Explained the daily limit.",
    "Customer updated their mobile number after changing provider. Verified through the app.",
])
def test_unsuccessful_checks_and_chatter_are_not_evidence(text):
    assert parse_kyc_note(text, "2024-01-01", "NOTE-Z") is None


# ----------------------------------------------------------------- residence statements

@pytest.mark.parametrize("text,expected", [
    ("Between 2011 and 2016 the account holder was a resident and employee in Finland.", ("Finland",)),
    ("The client kept a job and a home in Portugal from 2009 to 2012.", ("Portugal",)),
    ("Colombia served as the client's home and place of business from 2009 to 2021.", ("Colombia",)),
    ("Records show the user lived and was employed in Cote D'Ivoire between 2010 and 2014.",
     ("Cote D'Ivoire",)),
    ("Work and residency in the Netherlands were reported for 2015 to 2019.", ("Netherlands",)),
    ("During 2012 to 2016 Kenya was the customer's base for both life and business.", ("Kenya",)),
])
def test_residence_statements(text, expected):
    assert extract_residence_countries(text) == expected


@pytest.mark.parametrize("text", [
    "Customer requested a statement for a rental application. Sent the last three months.",
    "Customer called to ask how long an international transfer takes.",
])
def test_support_chatter_has_no_residence(text):
    assert extract_residence_countries(text) == ()


def test_canonical_country_hint():
    assert canonical_country("Dutch") == "Netherlands"
    assert canonical_country("GB") == "United Kingdom"
    assert canonical_country("Côte d'Ivoire") == canonical_country("Cote D'Ivoire")
    assert canonical_country("International") is None
    assert canonical_country("Atlantis") == "Atlantis"  # unknown stays, never dropped
