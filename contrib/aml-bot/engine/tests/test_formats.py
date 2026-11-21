"""Input-format tolerance tests: the official schema is not published, so the same small case is written in
different shapes and must give the same decision, and anything unmappable must never clear.
Run: python3 -m unittest discover -s system/tests -v"""
import contextlib, csv, io, json, os, shutil, sys, tempfile, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import screen, loader  # noqa: E402
from test_rules import World, VARIANTS  # noqa: E402


def case_tables():
    """One customer with a verified DOB of 1965 and one hit born 1950: a clean DOB_MISMATCH clearance."""
    w = World()
    rid, acc = w.customer("Tim Villagomez", "1965-03-28", kyc=("TIM VILLAGOMEZ", "1965-03-28", "United Kingdom"))
    w.alert("Tim Villagomez", [{"name": "Villagomez, Tim", "dobs": "1950", "countries": "United Kingdom"}],
            account=acc)
    shutil.rmtree(w.dir, ignore_errors=True)
    return w.t


def run(folder):
    out = os.path.join(folder, "_out.jsonl")
    with contextlib.redirect_stderr(io.StringIO()):
        screen.main(folder, out)
    with open(out, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh]


def write_xlsx(path, sheets):
    """Tiny stdlib xlsx writer (inline strings) for tests."""
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    esc = lambda v: str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        sh = "".join(f'<sheet name="{esc(n)}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i, n in enumerate(sheets))
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + sh + "</sheets></workbook>")
        rels = "".join(f'<Relationship Id="rId{i+1}" Target="worksheets/sheet{i+1}.xml"/>' for i in range(len(sheets)))
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + "</Relationships>")
        for i, (n, rows) in enumerate(sheets.items()):
            keys = list(dict.fromkeys(k for r in rows for k in r))
            grid = [["Exported from core banking"], keys] + [[r.get(k) for k in keys] for r in rows]
            xml = []
            for ri, row in enumerate(grid, 1):
                cells = "".join(
                    f'<c r="{col(ci)}{ri}" t="inlineStr"><is><t>{esc("; ".join(v) if isinstance(v, list) else v)}</t></is></c>'
                    for ci, v in enumerate(row) if v not in (None, ""))
                xml.append(f'<row r="{ri}">{cells}</row>')
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                       + "".join(xml) + "</sheetData></worksheet>")


class Formats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = case_tables()
        with open(os.path.join(self.dir, "name_variants.json"), "w") as fh:
            json.dump(VARIANTS, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def jsonl(self, t, rows, name=None):
        with open(os.path.join(self.dir, name or t + ".jsonl"), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def decision(self):
        out = run(self.dir)
        self.assertEqual(len(out), 1)
        return out[0]["decision"], out[0]["reason_code"]

    def test_baseline_jsonl(self):
        for t, rows in self.t.items():
            self.jsonl(t, rows)
        self.assertEqual(self.decision(), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_csv_and_json_array_and_camel_case(self):
        for i, (t, rows) in enumerate(self.t.items()):
            camel = [{(k.split("_")[0] + "".join(x.title() for x in k.split("_")[1:])): v for k, v in r.items()}
                     for r in rows]
            if i % 2:
                keys = list(dict.fromkeys(k for r in camel for k in r))
                with open(os.path.join(self.dir, t.title() + ".csv"), "w", newline="") as fh:
                    w = csv.writer(fh, delimiter=";")
                    w.writerow(keys)
                    for r in camel:
                        w.writerow(["; ".join(r[k]) if isinstance(r.get(k), list) else r.get(k) for k in keys])
            else:
                with open(os.path.join(self.dir, t + ".json"), "w") as fh:
                    json.dump(camel, fh)
        self.assertEqual(self.decision(), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_xlsx_workbook_with_title_row(self):
        write_xlsx(os.path.join(self.dir, "Screening Dev Set.xlsx"),
                   {t.replace("_", " ").title(): rows for t, rows in self.t.items()})
        self.assertEqual(self.decision(), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_hits_nested_in_alerts(self):
        hits = {h["hit_id"]: h for h in self.t["hits"]}
        alerts = []
        for a in self.t["alerts"]:
            a = dict(a)
            a["hits"] = [hits[h] for h in a.pop("hit_ids")]
            alerts.append(a)
        self.jsonl("alerts", alerts)
        for t in ("customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"):
            self.jsonl(t, self.t[t])
        self.assertEqual(self.decision(), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_flat_hit_rows_carry_the_alert(self):
        a = self.t["alerts"][0]
        rows = [dict(h, customer_name=a["customer_name"], account_ref=a["account_ref"],
                     screened_at=a["screened_at"]) for h in self.t["hits"]]
        self.jsonl("hits", rows)
        for t in ("customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"):
            self.jsonl(t, self.t[t])
        self.assertEqual(self.decision(), ("DIFFERENT_ENTITY", "DOB_MISMATCH"))

    def test_unmappable_critical_field_never_clears(self):
        for t, rows in self.t.items():
            if t == "kyc_checks":
                rows = [{("xx_" + k if k == "verified_dob" else k): v for k, v in r.items()} for r in rows]
            self.jsonl(t, rows)
        d, code = self.decision()
        self.assertEqual(d, "REVIEW")

    def test_missing_table_never_clears(self):
        for t, rows in self.t.items():
            if t != "kyc_checks":
                self.jsonl(t, rows)
        self.assertEqual(self.decision()[0], "REVIEW")

    def test_unknown_check_result_never_clears(self):
        for t, rows in self.t.items():
            if t == "kyc_checks":
                rows = [dict(r, result="GREEN") for r in rows]
            self.jsonl(t, rows)
        self.assertEqual(self.decision()[0], "REVIEW")


class FieldMapping(unittest.TestCase):
    def test_synonyms_and_nesting(self):
        r = loader.normalise_record("customers.jsonl", {"customerId": "C-1", "legalName": "A B", "birthDate": 25000,
                                                         "contact": {"email": "a@b.c"}, "type": "Natural Person"})
        self.assertEqual((r["record_id"], r["full_name"], r["dob"], r["email"], r["entity_type"]),
                         ("C-1", "A B", "1968-06-11", "a@b.c", "Individual"))

    def test_unknown_entity_type_is_unknown(self):
        self.assertIsNone(loader.normalise_record("hits.jsonl", {"type": "Vessel"})["entity_type"])

    def test_check_results(self):
        f = lambda v: loader.normalise_record("kyc_checks.jsonl", {"status": v})["result"]
        self.assertEqual((f("Passed"), f("FAILED"), f("Expired")), ("approved", "rejected", "expired"))
        self.assertTrue(f("GREEN").startswith("unrecognised"))

    def test_hit_ids_separators(self):
        self.assertEqual(loader.normalise_record("alerts.jsonl", {"hits": "H-1, H-2|H-3"})["hit_ids"],
                         ["H-1", "H-2", "H-3"])


if __name__ == "__main__":
    unittest.main()
