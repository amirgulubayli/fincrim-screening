#!/usr/bin/env python3
"""Three-case walkthrough for judges: one clearance, one confirmed match, one escalation.

    system/demo <data folder>            (e.g. system/demo dev)

Runs the real engine on three development-set alerts and prints the decision and reason in plain English.
"""
import contextlib, io, os, sys, textwrap, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screen  # noqa: E402

CASES = [
    ("H-58A39C", "A clear mismatch: cleared automatically",
     "The names match, but the bank's verified identity check proves a different birth date."),
    ("H-4D76E3", "A real match: sent to the compliance team",
     "The customer's own record says 1963, but a newer verified KYC check says 1968, which matches the listing. "
     "Verified beats declared, so the system does NOT clear it."),
    ("H-B1018D", "Not enough to decide: escalated to an analyst, with guidance",
     "The countries differ, but Serbia and Montenegro were one state, so the difference proves nothing."),
]
LABEL = {"DIFFERENT_ENTITY": "CLEARED (different person)", "SAME_ENTITY": "MATCH (same person, to compliance)",
         "REVIEW": "ESCALATED (an analyst decides)"}


def wrap(s, indent="    "):
    return textwrap.fill(s, 100, initial_indent=indent, subsequent_indent=indent)


def main(folder):
    t0 = time.time()
    print("Loading the bank's records and the watchlist hits ...")
    screen.loader.reset()
    with contextlib.redirect_stderr(io.StringIO()):
        D = screen.Data(folder)
    print(f"  {len(D.alerts)} alerts, {len(D.hits)} hits, {len(D.customers):,} customer records "
          f"({time.time() - t0:.1f}s)\n")
    alerts = {a["alert_id"]: a for a in D.alerts}
    for n, (hid, title, why) in enumerate(CASES, 1):
        hit = D.hits.get(hid)
        if not hit:
            print(f"Case {n}: {hid} is not in this data folder, skipped.\n")
            continue
        alert = alerts[hit["alert_id"]]
        persons, how = screen.resolve_customer(D, alert)
        r = screen.decide(D, alert, hit, persons, how)
        if r["decision"] == "DIFFERENT_ENTITY":
            r = screen.second_check(D, alert, hit, persons, r)
        if r["decision"] == "DIFFERENT_ENTITY":
            r = screen.final_gate(D, alert, hit, persons, r)
        one = screen.regulator_sentence(D, alert, hit, persons, r) if r["decision"] != "REVIEW" else None
        print("=" * 100)
        print(f"Case {n}: {title}")
        print(wrap(why))
        print(f"  Customer screened : {alert.get('customer_name')}  (alert {alert['alert_id']}, found via {how})")
        print(f"  Watchlist hit     : {hit.get('name')}  ({hit.get('primary_category') or hit.get('categories')})")
        print(f"  Decision          : {LABEL[r['decision']]}   code {r['reason_code']}")
        print("  Why:")
        print(wrap(r["reason"], "      "))
        print(f"  Records relied on : {', '.join(r['evidence']) or 'none'}")
        if one:
            print("  One-line audit reason:")
            print(wrap(one, "      "))
        if r["decision"] == "DIFFERENT_ENTITY":
            print("  Independent second check: reproduced the contradiction from the raw records, clearance kept.")
        print()
    print(f"Done in {time.time() - t0:.1f}s. The decisions come from the same engine that writes "
          f"output/dev.jsonl; the engine itself contains no case IDs (this demo only picks which three to show).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
