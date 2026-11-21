"""Tests for the three late safeguards: adversarial name/date tolerance, explain-or-escalate gate and
many-to-many context. Run: python3 -m unittest discover -s system/tests -v"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import screen  # noqa: E402
from test_rules import World, one  # noqa: E402


class Adversarial(unittest.TestCase):
    def test_ambiguous_day_month_needs_both_readings_to_contradict(self):
        d = screen.parse_date("03/04/1980", "dmy")
        self.assertEqual(tuple(d), (1980, 4, 3))
        self.assertFalse(screen.dob_contradicts(d, (1980, 3, 4)))      # the US reading matches
        self.assertTrue(screen.dob_agrees(d, (1980, 3, 4)))
        self.assertTrue(screen.dob_contradicts(d, (1980, 7, None)))

    def test_us_order_customer_dob_does_not_clear(self):
        r = one(self, dict(name="Tim Villagomez", dob="1965-03-28", kyc=("TIM VILLAGOMEZ", "03/04/1965", "United Kingdom")),
                {"name": "Tim Villagomez", "dobs": "1965-03-04", "countries": "United Kingdom"})
        self.assertNotEqual(r["decision"], "DIFFERENT_ENTITY")

    def test_romanisation_and_typo_links(self):
        n = screen.Names({"given_name_groups": []})
        for a, b in (("ali", "aly"), ("yusuf", "yousef"), ("hussein", "husain"), ("christohper", "christophar")):
            self.assertTrue(n.variant_link(a, b), (a, b))
        self.assertFalse(n.variant_link("david", "michael"))

    def test_shared_name_part_and_dob_block_location_clearance(self):
        r = one(self, dict(name="Sarah Okafor", dob="1980-05-05", country="United Kingdom",
                           kyc=("SARAH OKAFOR", "1980-05-05", "United Kingdom")),
                {"name": "Sarah Haddad", "dobs": "1980", "countries": "Lebanon"})
        self.assertEqual(r["decision"], "REVIEW")
        self.assertIn("surname", r["reason"])


class ExplainOrEscalate(unittest.TestCase):
    def test_clearance_has_a_one_sentence_reason(self):
        w = World()
        rid, acc = w.customer("Tim Villagomez", "1965-03-28", kyc=("TIM VILLAGOMEZ", "1965-03-28", "United Kingdom"))
        (hid,) = w.alert("Tim Villagomez", [{"name": "Tim Villagomez", "dobs": "1950"}], account=acc)
        screen.EXPLAIN = []
        try:
            r = w.run()[hid]
            self.assertEqual(r["decision"], "DIFFERENT_ENTITY")
            s = screen.EXPLAIN[0]["regulator_reason"]
            self.assertIn("verified KYC check", s)
            self.assertIn("1965-03-28", s)
            self.assertIn("1950", s)
        finally:
            screen.EXPLAIN = None

    def test_unexplainable_clearance_is_downgraded(self):
        w = World()
        rid, acc = w.customer("Tim Villagomez", "1965-03-28", kyc=("TIM VILLAGOMEZ", "1965-03-28", "United Kingdom"))
        (hid,) = w.alert("Tim Villagomez", [{"name": "Tim Villagomez", "dobs": "1950"}], account=acc)
        with mock.patch.object(screen, "regulator_sentence", return_value=None):
            r = w.run()[hid]
        self.assertEqual(r["decision"], "REVIEW")
        self.assertIn("concrete conflicting value", r["reason"])


class ManyToMany(unittest.TestCase):
    def test_common_name_context_on_reviews_only(self):
        w = World()
        hids = []
        for i in range(4):
            rid, acc = w.customer("Abdul Rahman", f"19{60 + i}-01-01", country="United Kingdom")
            hids += w.alert("Abdul Rahman", [{"name": "Abdul Rahman", "countries": "United Kingdom"}], account=acc)
        res = w.run()
        for h in hids:
            self.assertEqual(res[h]["decision"], "REVIEW")
            self.assertIn("common-name hit", res[h]["reason"])


if __name__ == "__main__":
    unittest.main()

