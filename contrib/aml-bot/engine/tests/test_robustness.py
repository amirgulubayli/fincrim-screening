"""Robustness tests: fail-safe parsing, tolerant loader, crash-proofing, the second check and perturbation
invariants.  Run: python3 -m unittest discover -s system/tests -v"""
import contextlib, io, json, os, shutil, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import screen  # noqa: E402
from test_rules import World, one, VARIANTS  # noqa: E402


def run_folder(folder):
    out = os.path.join(folder, "out.jsonl")
    screen.LOAD_WARNINGS.clear()
    with contextlib.redirect_stderr(io.StringIO()):
        screen.main(folder, out)
    with open(out, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh]


class FailSafeParsing(unittest.TestCase):
    def test_unparseable_dates_are_unknown(self):
        for v in ("04/05/90", "5th of May, ninety", "1990-13-01", "31/02/1990", "0000-00-00", "", None, [], "1066"):
            self.assertIsNone(screen.parse_date(v), v)

    def test_date_formats(self):
        for v in ("1990-05-04", "1990/05/04", "04.05.1990", "4 May 1990", "May 4, 1990", "4th May 1990",
                  "04-May-1990", "1990-05-04T00:00:00Z", "May 4 1990"):
            self.assertEqual(screen.parse_date(v), (1990, 5, 4), v)
        self.assertEqual(screen.parse_date("05/25/1990"), (1990, 5, 25))          # unmistakably month-first

    def test_ambiguous_day_month_keeps_only_year_when_unsure(self):
        self.assertEqual(screen.parse_date("04/05/1990", "year"), (1990, None, None))
        old = screen.AMBIGUOUS_DMY_POLICY
        try:
            self.assertEqual(screen.detect_day_first([{"dob": "05/25/1990"}]), "year")
            self.assertEqual(screen.detect_day_first([{"dob": "25/05/1990"}, {"dob": "04/05/1990"}]), "dmy")
        finally:
            screen.AMBIGUOUS_DMY_POLICY = old

    def test_country_codes_and_unknowns(self):
        for v in ("GBR", "gb", "U.K.", "the United Kingdom", "Great Britain", "BRITISH"):
            self.assertEqual(screen.country_code(v), "GB", v)
        self.assertEqual(screen.country_code("Côte d’Ivoire"), "CI")
        self.assertEqual(screen.country_code("Atlantis"), screen.UNKNOWN_COUNTRY)
        self.assertIsNone(screen.country_code(""))
        self.assertEqual(screen.country_code("12 Rue X, Lyon, France [Residential]", scan=True), "FR")

    def test_unknown_country_never_clears_on_location(self):
        r = one(self, dict(name="Marcus Miller", dob="1979-08-14", country="Germany"),
                {"name": "Marcus Miller", "countries": "Atlantis"})
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")
        r = one(self, dict(name="Marcus Miller", dob="1979-08-14", country="Wakanda"),
                {"name": "Marcus Miller", "countries": "United States"})
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")

    def test_unreadable_dob_blocks_dob_and_location_clearance(self):
        r = one(self, dict(name="Tim Villagomez", dob="28/03/65"), {"name": "Tim Villagomez", "dobs": "1950"})
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")
        r = one(self, dict(name="Marcus Miller", dob="14/08/79", country="Germany"),
                {"name": "Marcus Miller", "countries": "United States", "dobs": "1979"})
        self.assertEqual(r["decision"], "REVIEW")
        r = one(self, dict(name="Tim Villagomez", dob="1965-03-28"),
                {"name": "Tim Villagomez", "dobs": "1950; 03/28/65"})
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")

    def test_misspelt_variant_blocks_surname_clearance(self):
        n = screen.Names({"given_name_groups": [["christopher", "cristobal", "chris"]]})
        self.assertTrue(n.variant_link("christohper", "cristobal"))
        self.assertFalse(n.variant_link("sarah", "james"))


class TolerantLoader(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_bad_lines_bom_and_aliases(self):
        with open(os.path.join(self.dir, "hits.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\ufeff" + json.dumps({"ID": "H-1", "Alert ID": "A-1", "Full Name": "Jo Bloggs",
                                            "Date of Birth": ["1980", "1981"], "AKA": ["J Bloggs"],
                                            "Address": [{"line": "1 Rue X, Paris, France", "type": "Business"}]})
                     + "\n{broken json\n[1, 2]\n\n")
        rows = screen.load_jsonl(self.dir, "hits.jsonl")
        self.assertEqual(len(rows), 1)
        h = rows[0]
        self.assertEqual((h["hit_id"], h["alert_id"], h["name"], h["dobs"], h["aliases"]),
                         ("H-1", "A-1", "Jo Bloggs", "1980; 1981", "J Bloggs"))
        self.assertEqual(h["addresses"], "1 Rue X, Paris, France [Business]")
        self.assertEqual(len(screen.LOAD_WARNINGS) >= 2, True)

    def test_alias_never_overwrites_real_field(self):
        r = screen.normalise_record("hits.jsonl", {"dobs": "1980", "dob": "1990"})
        self.assertEqual(r["dobs"], "1980")

    def test_missing_files_and_variants(self):
        with open(os.path.join(self.dir, "alerts.jsonl"), "w") as fh:
            fh.write(json.dumps({"alert_id": "A-1", "customer_name": "Jo Bloggs", "hit_ids": ["H-1"]}) + "\n")
        with open(os.path.join(self.dir, "hits.jsonl"), "w") as fh:
            fh.write(json.dumps({"hit_id": "H-1", "alert_id": "A-1", "name": "Jo Bloggs"}) + "\n")
            fh.write(json.dumps({"hit_id": "H-2", "alert_id": "A-404", "name": "Orphan"}) + "\n")
        out = run_folder(self.dir)
        self.assertEqual(sorted(r["hit_id"] for r in out), ["H-1", "H-2"])
        self.assertTrue(all(r["decision"] == "REVIEW" for r in out))

    def test_empty_folder(self):
        self.assertEqual(run_folder(self.dir), [])


class CrashProofing(unittest.TestCase):
    def test_exception_in_a_hit_becomes_review(self):
        w = World()
        rid, acc = w.customer("Tim Villagomez", "1965-03-28")
        (hid,) = w.alert("Tim Villagomez", [{"name": "Tim Villagomez", "dobs": "1950"}], account=acc)
        with mock.patch.object(screen, "decide", side_effect=RuntimeError("boom")):
            r = w.run()[hid]
        self.assertEqual((r["decision"], r["reason_code"]), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertIn("could not complete", r["reason"])

    def test_exception_in_customer_lookup_becomes_review(self):
        w = World()
        rid, acc = w.customer("Tim Villagomez", "1965-03-28")
        (hid,) = w.alert("Tim Villagomez", [{"name": "Tim Villagomez", "dobs": "1950"}], account=acc)
        with mock.patch.object(screen, "resolve_customer", side_effect=KeyError("x")):
            r = w.run()[hid]
        self.assertEqual(r["decision"], "REVIEW")


class SecondCheck(unittest.TestCase):
    def test_reproducible_clearance_kept(self):
        r = one(self, dict(name="Tim Villagomez", dob="1965-03-28"), {"name": "Tim Villagomez", "dobs": "1950"})
        self.assertEqual(r["decision"], "DIFFERENT_ENTITY")

    def test_unreproducible_clearance_downgraded(self):
        w = World()
        rid, acc = w.customer("Tim Villagomez", "1965-03-28")
        (hid,) = w.alert("Tim Villagomez", [{"name": "Tim Villagomez", "dobs": "1965"}], account=acc)
        fake = lambda *a, **k: {"decision": "DIFFERENT_ENTITY", "reason_code": "DOB_MISMATCH",
                                "customer_record_id": rid, "hit_id": hid, "alert_id": "A-000000",
                                "reason": "first pass (deliberately wrong)", "evidence": [rid]}
        with mock.patch.object(screen, "decide", side_effect=fake):
            r = w.run()[hid]
        self.assertEqual((r["decision"], r["reason_code"]), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertIn("second check", r["reason"])

    def test_possible_same_person_with_partial_dob_blocks_clearance(self):
        w = World()
        rid, acc = w.customer("David Bowman", "1962-10-23", country="Germany", phone="+1 5550001")
        w.customer("David Bowman", "1962", country="United States", phone="+1 5550001")
        (hid,) = w.alert("David Bowman", [{"name": "Bowman, David", "countries": "United States"}], account=acc)
        r = w.run()[hid]
        self.assertEqual(r["decision"], "REVIEW")


class PerturbationInvariants(unittest.TestCase):
    """The same case written differently must never turn into a clearance."""
    BASE = (dict(name="Marcus Miller", dob="1979-08-14", country="Germany",
                 kyc=("MARCUS MILLER", "1979-08-14", "Germany")),
            {"name": "Marcus Miller", "countries": "United States", "dobs": "1979"})

    def check(self, cust, hit):
        r = one(self, cust, hit)
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY", (cust, hit, r["reason"]))

    def test_variants_of_a_review_case_stay_uncleared(self):
        cust, hit = self.BASE
        self.check(cust, hit)
        self.check(cust, dict(hit, name="MILLER, Marcus"))
        self.check(cust, dict(hit, name="Märcus Millér"))
        self.check(cust, dict(hit, countries="USA"))
        self.check(cust, dict(hit, dobs="14 August 1979"))
        self.check(dict(cust, dob="14/08/79"), hit)
        self.check(dict(cust, dob="08/14/1979"), hit)
        self.check(dict(cust, kyc=("MARCUS MILLER", "79-08-14", "Germany")), hit)
        self.check(dict(cust, country="DEU"), dict(hit, countries="united states of america"))


if __name__ == "__main__":
    unittest.main()
