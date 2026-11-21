"""Regenerate tests/fixtures/mini/*.jsonl (tiny invented dataset for unit tests).

    python tests/fixtures/make_mini.py
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "mini"

DATA = {
    "alerts": [
        {"alert_id": "A-1", "screened_at": "2026-11-06", "customer_name": "Jane Example",
         "entity_type": "Individual", "dob": "1980-01-02", "account_ref": "ACC-1",
         "hit_count": 2, "hit_ids": "H-1; H-2"},
        {"alert_id": "A-2", "screened_at": "2026-11-06", "customer_name": "Joint Holder",
         "entity_type": "Individual", "account_ref": "ACC-2", "hit_count": 1, "hit_ids": ["H-3"]},
    ],
    "hits": [
        {"hit_id": "H-1", "alert_id": "A-1", "match_score": 95, "entity_type": "Company",
         "name": "Jane Example Ltd", "countries": "United Kingdom"},
        {"hit_id": "H-2", "alert_id": "A-1", "match_score": 99, "entity_type": "Individual",
         "name": "EXAMPLE, Jane", "dobs": "1980; 1981", "countries": "United Kingdom"},
        {"hit_id": "H-3", "alert_id": "A-2", "match_score": 90, "entity_type": "Company",
         "name": "Joint Holder SA", "countries": "Spain"},
    ],
    "customers": [
        {"record_id": "CUS-1", "entity_type": "Individual", "full_name": "Jane Example",
         "dob": "1980-01-02", "nationality": "British", "residence_country": "GB",
         "email": "jane@example.test", "phone": "+44 1"},
        {"record_id": "CUS-2", "entity_type": "Individual", "full_name": "Joint Holder",
         "dob": "1970-05-05", "email": "a@example.test", "phone": "+44 2"},
        {"record_id": "CUS-3", "entity_type": "Individual", "full_name": "Joint Holder",
         "dob": "1999-09-09", "email": "b@example.test", "phone": "+44 3"},
    ],
    "accounts": [
        {"account_id": "ACC-1", "customer_id": "CUS-1", "login_handle": "janee1"},
        {"account_id": "ACC-2", "customer_id": "CUS-2", "login_handle": "jointa"},
        {"account_id": "ACC-2", "customer_id": "CUS-3", "login_handle": "jointb"},
    ],
    "onboarding": [
        {"account_id": "ACC-1", "customer_id": "CUS-1", "kyc_applicant_id": "APP-1"},
    ],
    "kyc_checks": [
        {"check_id": "KYC-1", "applicant_id": "APP-1", "verified_name": "Jane Example",
         "verified_dob": "1980-01-02", "document_country": "United Kingdom",
         "result": "approved", "checked_at": "2024-01-02"},
        {"check_id": "KYC-2", "applicant_id": "APP-1", "verified_name": "Jane Exampel",
         "verified_dob": "1980-01-02", "document_country": "United Kingdom",
         "result": "rejected", "checked_at": "2024-03-01"},
    ],
    "notes": [
        {"note_id": "NOTE-1", "requester_email": "jane@example.test", "created_at": "2024-01-02",
         "channel": "kyc", "text": "Identity verified."},
    ],
    "search_results": [],
    "practice_labels": [
        {"hit_id": "H-1", "alert_id": "A-1", "decision": "DIFFERENT_ENTITY",
         "reason_codes": "ENTITY_TYPE_MISMATCH", "customer_record_ids": "CUS-1", "evidence": "CUS-1"},
        {"hit_id": "H-2", "alert_id": "A-1", "decision": "SAME_ENTITY",
         "reason_codes": "NAME_DOB_LOCATION_MATCH; NAME_DOB_MATCH",
         "customer_record_ids": "CUS-1", "evidence": "CUS-1; KYC-1"},
    ],
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in DATA.items():
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    print(f"wrote {len(DATA)} files to {OUT}")
