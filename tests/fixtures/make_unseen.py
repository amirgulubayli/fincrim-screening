"""Regenerate tests/fixtures/unseen/*.jsonl: hand-made "unseen-style" cases (all invented).

Each alert is a true or plausible match written the way the unseen set might write it:
new KYC-note wording, date formats that do not occur in dev, countries spelled differently,
an unreadable hit DOB, plus a BOM and a junk line in notes.jsonl. None of them may be
cleared (DIFFERENT_ENTITY): the missing / unreadable fact must push towards REVIEW.

    python tests/fixtures/make_unseen.py
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "unseen"


def ind(alert_id, hit_id, name, dob, acc, **kw):
    return {"alert_id": alert_id, "screened_at": "2026-11-06", "customer_name": name,
            "entity_type": "Individual", "dob": dob, "country": None, "city": None,
            "account_ref": acc, "login_handle": None, "email": None, "hit_count": 1,
            "hit_ids": hit_id, **kw}


def hit(hit_id, alert_id, name, dobs, **kw):
    base = {"hit_id": hit_id, "alert_id": alert_id, "match_score": 97, "entity_type": "Individual",
            "name": name, "aliases": None, "gender": None, "dobs": dobs, "place_of_birth": None,
            "life_status": None, "deceased_date": None, "nationality": None, "citizenship": None,
            "countries": None, "addresses": None, "identifiers": None, "categories": "Sanctions",
            "primary_category": None, "position_or_allegation": None, "details": None,
            "relationships": None, "profile_notes": None, "sources": None, "source_urls": None}
    base.update(kw)
    return base


def cus(rid, name, dob, nat, res, email, phone):
    return {"record_id": rid, "entity_type": "Individual", "full_name": name, "preferred_name": None,
            "login_name": None, "dob": dob, "nationality": nat, "residence_country": res, "city": None,
            "address": None, "email": email, "phone": phone, "occupation": None, "employer": None,
            "registration_number": None, "onboarded_at": "2024-01-10", "status": "active",
            "last_kyc_refresh": None}


DATA = {
    "alerts": [
        # U1 compact / Roman-numeral DOB formats + "Rep. of Korea" (unrecognised spelling)
        ind("A-U1", "H-U1", "Minho Testkim", "19750304", "ACC-U1"),
        # U2 DOB only in a KYC note with new wording; customer record holds no DOB
        ind("A-U2", "H-U2", "Odera Fictionwu", None, "ACC-U2"),
        # U3 hit DOB present but unreadable ("circa 1971"); locations differ
        ind("A-U3", "H-U3", "Tamsin Placeholder", "1971-06-30", "ACC-U3"),
        # U4 two-digit-year DOB with dashes + "Türkiye" vs "Turkey"
        ind("A-U4", "H-U4", "Deniz Ornekoglu", "04-03-75", "ACC-U4"),
        # U5 hit without DOB, country spelled in a way nobody recognises vs a known customer country
        ind("A-U5", "H-U5", "Lukas Mustermannson", "1983-09-12", "ACC-U5"),
    ],
    "hits": [
        hit("H-U1", "A-U1", "TESTKIM, Minho", "1975-03-04", nationality="Rep. of Korea"),
        hit("H-U2", "A-U2", "Odera Fictionwu", "1971", nationality="Nigeria"),
        hit("H-U3", "A-U3", "Tamsin Placeholder", "circa 1971", countries="Nigeria"),
        hit("H-U4", "A-U4", "Deniz Ornekoglu", "1975", countries="Turkey"),
        hit("H-U5", "A-U5", "Lukas Mustermannson", None, countries="Rep. of Korea"),
    ],
    "customers": [
        cus("CUS-U1", "Minho Testkim", "19750304", "South Korea", "South Korea",
            "minho.t@unseen.test", "+82 100000001"),
        cus("CUS-U2", "Odera Fictionwu", None, None, None, "odera.f@unseen.test", "+234 100000002"),
        cus("CUS-U3", "Tamsin Placeholder", "1971-06-30", "United Kingdom", "United Kingdom",
            "tamsin.p@unseen.test", "+44 100000003"),
        cus("CUS-U4", "Deniz Ornekoglu", "04-03-75", "Türkiye", "Türkiye",
            "deniz.o@unseen.test", "+90 100000004"),
        cus("CUS-U5", "Lukas Mustermannson", "1983-09-12", "Germany", "Germany",
            "lukas.m@unseen.test", "+49 100000005"),
    ],
    "accounts": [
        {"account_id": f"ACC-U{i}", "customer_id": f"CUS-U{i}", "login_handle": None,
         "phone": None, "opened_at": "2024-01-10", "status": "active"} for i in range(1, 6)
    ],
    "onboarding": [
        {"account_id": f"ACC-U{i}", "customer_id": f"CUS-U{i}", "kyc_applicant_id": f"APP-U{i}",
         "onboarded_at": "2024-01-10"} for i in (1, 3, 4)
    ],
    "kyc_checks": [
        {"check_id": "KYC-U1", "applicant_id": "APP-U1", "provider": "Veriff",
         "verified_name": "Minho Testkim", "verified_dob": "04.III.1975", "document_type": "passport",
         "document_number": "X0000001", "document_country": "Rep. of Korea", "result": "approved",
         "checked_at": "2024-01-10"},
        {"check_id": "KYC-U3", "applicant_id": "APP-U3", "provider": "Onfido",
         "verified_name": "Tamsin Placeholder", "verified_dob": "1971-06-30",
         "document_type": "passport", "document_number": "X0000003",
         "document_country": "United Kingdom", "result": "approved", "checked_at": "2024-01-10"},
        {"check_id": "KYC-U4", "applicant_id": "APP-U4", "provider": "Sumsub",
         "verified_name": "Deniz Ornekoglu", "verified_dob": "04-03-75", "document_type": "national ID",
         "document_number": "X0000004", "document_country": "Republic of Türkiye",
         "result": "approved", "checked_at": "2024-01-10"},
    ],
    "notes": [
        {"note_id": "NOTE-U2", "requester_email": "odera.f@unseen.test", "created_at": "2024-01-10",
         "channel": "kyc",
         "text": "ID check passed (provider: Veriff). Holder: ODERA FICTIONWU. Birth date per "
                 "document: 7th of March 1971. Issuing state: Federal Republic of Nigeria."},
    ],
    "search_results": [],
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in DATA.items():
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            if name == "notes":  # BOM + a junk line: the loader must skip it, not crash
                fh.write("﻿")
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if name == "notes":
                fh.write("{this line is not json\n")
    print(f"wrote {len(DATA)} files to {OUT}")
