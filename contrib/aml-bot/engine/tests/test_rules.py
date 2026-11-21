"""Edge-case tests for the identity rules. Run: python3 -m unittest discover -s system/tests -v"""
import json, os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import screen  # noqa: E402

VARIANTS = {"given_name_groups": [["muhammad", "mohammed", "mohammad", "mohamed", "mohd"],
                                  ["alexander", "alex", "aleksandr", "sasha"], ["william", "will", "bill"],
                                  ["umar", "omar"], ["yusuf", "joseph", "joe"]],
            "company_legal_forms": ["ltd", "limited", "gmbh", "sa", "plc", "inc", "llc"]}


class World:
    """Builds a tiny data folder: each customer gets one account, one onboarding row and optional KYC/notes."""
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.t = {k: [] for k in ("alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes",
                                  "search_results")}
        self.n = 0

    def customer(self, name, dob, country="United Kingdom", nationality=None, etype="Individual", phone=None,
                 city="London", kyc=None, notes=()):
        self.n += 1
        rid, acc, app = f"CUS-{self.n:06d}", f"ACC-{self.n:06d}", f"APP-{self.n:06d}"
        email = f"c{self.n}@x.example"
        self.t["customers"].append({"record_id": rid, "entity_type": etype, "full_name": name, "preferred_name": None,
                                    "login_name": None, "dob": dob, "nationality": nationality or country,
                                    "residence_country": country, "city": city, "address": f"1 High St, {city}",
                                    "email": email, "phone": phone or f"+44 7{self.n:09d}"})
        self.t["accounts"].append({"account_id": acc, "customer_id": rid, "login_handle": f"login{self.n}",
                                   "phone": phone, "status": "active"})
        self.t["onboarding"].append({"account_id": acc, "customer_id": rid, "kyc_applicant_id": app})
        if kyc:
            vname, vdob, vcountry = kyc
            self.t["kyc_checks"].append({"check_id": f"KYC-{self.n:06d}", "applicant_id": app, "provider": "Onfido",
                                         "verified_name": vname, "verified_dob": vdob, "document_type": "passport",
                                         "document_number": f"P{self.n:07d}", "document_country": vcountry,
                                         "result": "approved", "checked_at": "2024-01-01"})
        for i, (ch, text) in enumerate(notes):
            self.t["notes"].append({"note_id": f"NOTE-{self.n:04d}{i:02d}", "requester_email": email,
                                    "created_at": "2025-01-01", "channel": ch, "text": text})
        return rid, acc

    def alert(self, name, hits, account=None, dob=None, country=None, etype="Individual"):
        aid = f"A-{len(self.t['alerts']):06d}"
        ids = []
        for j, h in enumerate(hits):
            hid = f"H-{len(self.t['hits']):06d}"
            base = {"hit_id": hid, "alert_id": aid, "entity_type": "Individual", "name": name, "aliases": None,
                    "dobs": None, "countries": None, "nationality": None, "citizenship": None, "addresses": None,
                    "place_of_birth": None, "deceased_date": None, "categories": "PEP", "primary_category": "PEP",
                    "profile_notes": None}
            base.update(h)
            self.t["hits"].append(base)
            ids.append(hid)
        self.t["alerts"].append({"alert_id": aid, "screened_at": "2026-11-10", "customer_name": name,
                                 "entity_type": etype, "dob": dob, "country": country, "city": None,
                                 "account_ref": account, "login_handle": None, "email": None, "hit_ids": ids})
        return ids

    def run(self):
        for k, rows in self.t.items():
            with open(os.path.join(self.dir, k + ".jsonl"), "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
        with open(os.path.join(self.dir, "name_variants.json"), "w") as fh:
            json.dump(VARIANTS, fh)
        out = os.path.join(self.dir, "out.jsonl")
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            screen.main(self.dir, out)
        with open(out) as fh:
            res = {json.loads(l)["hit_id"]: json.loads(l) for l in fh}
        shutil.rmtree(self.dir)
        return res


def one(test, customer_kwargs, hit, alert_name=None, **alert_kwargs):
    w = World()
    rid, acc = w.customer(**customer_kwargs)
    (hid,) = w.alert(alert_name or customer_kwargs["name"], [hit], account=acc, **alert_kwargs)
    return w.run()[hid]


class Normalisation(unittest.TestCase):
    def test_dates_keep_partial(self):
        self.assertEqual(screen.parse_date("1990-05-04"), (1990, 5, 4))
        self.assertEqual(screen.parse_date("04/05/1990"), (1990, 5, 4))
        self.assertEqual(screen.parse_date("May 4, 1990"), (1990, 5, 4))
        self.assertEqual(screen.parse_date("4 May 1990"), (1990, 5, 4))
        self.assertEqual(screen.parse_date("1990"), (1990, None, None))
        self.assertEqual(screen.parse_date("1990-05"), (1990, 5, None))

    def test_countries(self):
        for v in ("GB", "United Kingdom", "British"):
            self.assertEqual(screen.country_code(v), "GB")
        self.assertIsNone(screen.country_code("International"))
        self.assertEqual(screen.country_code("Bailén 3, Madrid, Spain [Business]"), "ES")

    def test_dob_rules(self):
        self.assertTrue(screen.dob_agrees((1968, 3, 5), (1968, None, None)))
        self.assertFalse(screen.dob_contradicts((1968, 3, 5), (1967, None, None)))   # within 1 year
        self.assertTrue(screen.dob_contradicts((1968, 3, 5), (1966, None, None)))
        self.assertTrue(screen.dob_contradicts((1968, 3, 5), (1968, 4, None)))

    def test_names(self):
        n = screen.Names(VARIANTS)
        self.assertTrue(n.agree("Mohammed Ali Khan", "Muhammad Khan"))
        self.assertTrue(n.agree("Abdulsalam Bello", "Abdul Salam Bello"))
        self.assertFalse(n.agree("Sarah Johnson", "Johnson, James Jacob"))
        self.assertEqual(n.parts("RAHMAN TEST TEST"), ["rahman"])          # placeholders are not name parts
        self.assertEqual(n.parts("Acme Holdings Ltd", company=True), ["acme", "holdings"])

    def test_kyc_note_parsing(self):
        p = screen.parse_kyc_note({"note_id": "NOTE-1", "channel": "kyc",
                                   "text": "Onfido verified MARYAM UMAR, DOB January 21, 1980, on 2025-06-05, "
                                           "utilizing a national ID from Nigeria."})
        self.assertEqual((p["verified_name"], p["verified_dob"], p["checked_at"]), ("MARYAM UMAR", "1980-01-21", "2025-06-05"))
        self.assertEqual(screen.country_code(p["document_country"]), "NG")

    def test_age_extraction_ignores_units_and_other_people(self):
        self.assertEqual(screen.age_years("Michael Tilley, aged 46, was questioned in January 2023.", ["Michael Tilley"]),
                         {1976, 1977})
        self.assertEqual(screen.age_years("Police seized 40 pounds of drugs from Omar Hussein in May 2020.",
                                          ["Omar Hussein"]), set())


class Rules(unittest.TestCase):
    def test_entity_type_mismatch(self):
        r = one(self, dict(name="Mun Chen Ltd", dob="2001-01-01", etype="Company"),
                {"entity_type": "Individual", "name": "Mun Chen"}, etype="Company")
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH"))

    def test_deceased(self):
        r = one(self, dict(name="Jonathan Reyes", dob="1990-01-01"),
                {"name": "Jonathan Reyes", "deceased_date": "2022-03"})
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "DECEASED"))

    def test_too_young_at_earliest_report(self):
        r = one(self, dict(name="Abdul Rahman", dob="2007-08-21"),
                {"name": "Abdul Rahman", "countries": "United Kingdom",
                 "profile_notes": "According to Police; January 08, 2020: Abdul Rahman was arrested."})
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY"))

    def test_dob_mismatch_uses_every_dob(self):
        # record DOB contradicts, but an approved KYC check agrees -> must NOT clear (the H-4D76E3 lesson)
        r = one(self, dict(name="Mohammad Nadeem Ghani", dob="1963-12-02",
                           kyc=("MOHAMMAD NADEEM GHANI", "1968-02-17", "United Kingdom")),
                {"name": "Mohammad Nadeem Ghani", "dobs": "1968-02", "countries": "United Kingdom"})
        self.assertEqual((r["decision"], r["reason_code"]), ("SAME_ENTITY", "NAME_DOB_LOCATION_MATCH"))
        r = one(self, dict(name="Tim Villagomez", dob="1965-03-28"), {"name": "Tim Villagomez", "dobs": "1950"})
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_location_mismatch_and_name_dob_block(self):
        r = one(self, dict(name="Marcus Miller", dob="1979-08-14", country="Germany"),
                {"name": "Marcus Miller", "countries": "United States"})
        self.assertEqual((r["decision"], r["reason_code"]), ("DIFFERENT_ENTITY", "LOCATION_MISMATCH"))
        r = one(self, dict(name="Marcus Miller", dob="1979-08-14", country="Germany"),
                {"name": "Marcus Miller", "countries": "United States", "dobs": "1979; 1980"})
        self.assertEqual(r["decision"], "REVIEW")      # name + DOB agree: a country difference does not clear

    def test_note_country_blocks_location_clearance(self):
        r = one(self, dict(name="Abdul Rahman Yasin", dob="1965-05-19", country="Australia",
                           notes=[("email", "The account holder reported living in the United States between 2013 and 2023.")]),
                {"name": "Abdul Rahman Yasin", "countries": "United States"})
        self.assertEqual(r["decision"], "REVIEW")

    def test_business_address_only(self):
        r = one(self, dict(name="Abdul Rahman", dob="1990-01-01", country="Singapore"),
                {"name": "Abdul Rahman", "addresses": "1 Mall Road, Lahore, Pakistan [Business]"})
        self.assertEqual(r["decision"], "REVIEW")
        self.assertIn("business address", r["reason"])

    def test_former_single_state(self):
        r = one(self, dict(name="Dejan Jovanovic", dob="1980-01-01", country="Serbia"),
                {"name": "Jovanović, Dejan", "countries": "Montenegro"})
        self.assertEqual(r["decision"], "REVIEW")
        self.assertIn("Yugoslavia", r["reason"])
        r = one(self, dict(name="Dejan Jovanovic", dob="1980-01-01", country="Spain"),
                {"name": "Jovanović, Dejan", "countries": "Portugal"})     # neighbours never unified: clears
        self.assertEqual(r["reason_code"], "LOCATION_MISMATCH")

    def test_surname_middle_rule(self):
        cases = [("SARAH JOHNSON", "Johnson, James Jacob", "DIFFERENT_ENTITY"),   # given names clearly differ
                 ("SARAH JOHNSON", "Johnson, S. J.", "REVIEW"),                   # initials
                 ("ALEX PETROV", "Petrov, Aleksandr", "REVIEW"),                  # listed variant
                 ("ALEXANDRA PETROV", "Petrov, Alex", "REVIEW"),                  # prefix / diminutive
                 ("AZEEZ CHINEDU", "Chinedu Azeez Ridwan", "REVIEW")]              # possibly reordered
        for vname, hname, want in cases:
            r = one(self, dict(name=vname.title(), dob="1990-01-01", kyc=(vname, "1990-01-01", "United Kingdom")),
                    {"name": hname, "countries": "United Kingdom"})
            self.assertEqual(r["decision"], want, (vname, hname, r["reason"]))

    def test_patronymic_surname_goes_to_review(self):
        r = one(self, dict(name="Ibrahim Umar", dob="1990-01-01", country="Nigeria",
                           kyc=("IBRAHIM UMAR", "1990-01-01", "Nigeria")),
                {"name": "Umar, Mohammad", "countries": "Nigeria"})
        self.assertEqual(r["decision"], "REVIEW")
        self.assertIn("father's given name", r["reason"])

    def test_same_entity_company(self):
        r = one(self, dict(name="Pescanova SA", dob="1960-01-01", etype="Company", country="Spain"),
                {"entity_type": "Company", "name": "Pescanova, Sociedad Anónima S.A.", "countries": "Spain"},
                etype="Company")
        self.assertEqual((r["decision"], r["reason_code"]), ("SAME_ENTITY", "NAME_LOCATION_MATCH"))

    def test_ambiguous_unlinked_customers(self):
        w = World()
        w.customer("Juan Gonzalez", "1980-05-05", country="Panama")
        w.customer("Juan Gonzalez", "1980-05-05", country="Panama", kyc=("PEDRO GONZALEZ", "1980-05-05", "Panama"))
        (hid,) = w.alert("Juan Gonzalez", [{"name": "Gonzalez, Juan", "countries": "Panama"}],
                         dob="1980", country="Panama")
        r = w.run()[hid]
        self.assertEqual(r["customer_record_id"], "AMBIGUOUS")
        self.assertEqual((r["decision"], r["reason_code"]), ("REVIEW", "AMBIGUOUS_CUSTOMER_RECORD"))

    def test_same_person_merge_on_phone_and_dob(self):
        w = World()
        rid, acc = w.customer("David Bowman", "1962-10-23", country="United States", phone="+1 5550001")
        w.customer("David Bowman", "1962-10-23", country="United States", phone="+1 5550001",
                   kyc=("DAVID BOWMAN", "1962-10-23", "United States"))
        (hid,) = w.alert("David Bowman", [{"name": "Bowman, David", "dobs": "1962", "countries": "United States"}],
                         account=acc)
        r = w.run()[hid]
        self.assertEqual(r["decision"], "SAME_ENTITY")
        self.assertIn("KYC-000002", r["evidence"])     # the other record's verified check counts


if __name__ == "__main__":
    unittest.main()
