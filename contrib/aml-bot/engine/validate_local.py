#!/usr/bin/env python3
"""Local stand-in for the organisers' validate.py. Usage: validate_local.py <output.jsonl> [--score labels.jsonl] [--hits hits.jsonl]"""
import json, sys
DEC = {"DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"}
CODES = {"DIFFERENT_ENTITY": {"TEMPORAL_IMPOSSIBILITY", "DOB_MISMATCH", "LOCATION_MISMATCH", "NAME_MISMATCH_VERIFIED",
                              "ENTITY_TYPE_MISMATCH", "DECEASED"},
         "SAME_ENTITY": {"NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH", "NAME_LOCATION_MATCH"},
         "REVIEW": {"INSUFFICIENT_DATA", "AMBIGUOUS_CUSTOMER_RECORD"}}
COST = {("DIFFERENT_ENTITY", "DIFFERENT_ENTITY"): 0, ("DIFFERENT_ENTITY", "SAME_ENTITY"): 3, ("DIFFERENT_ENTITY", "REVIEW"): 3,
        ("SAME_ENTITY", "DIFFERENT_ENTITY"): 60, ("SAME_ENTITY", "SAME_ENTITY"): 0, ("SAME_ENTITY", "REVIEW"): 2,
        ("REVIEW", "DIFFERENT_ENTITY"): 60, ("REVIEW", "SAME_ENTITY"): 2, ("REVIEW", "REVIEW"): 0}  # (truth, predicted)

def lst(v):
    if v is None: return []
    return v if isinstance(v, list) else [x.strip() for x in str(v).split(";") if x.strip()]

def main():
    out = sys.argv[1]
    rows = [json.loads(l) for l in open(out) if l.strip()]
    errs = []
    for r in rows:
        for f in ("hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "reason", "evidence"):
            if f not in r: errs.append(f"{r.get('hit_id')}: missing {f}")
        if r.get("decision") not in DEC: errs.append(f"{r.get('hit_id')}: bad decision")
        elif r.get("reason_code") not in CODES[r["decision"]]: errs.append(f"{r['hit_id']}: code does not belong to decision")
        if not isinstance(r.get("evidence"), list): errs.append(f"{r.get('hit_id')}: evidence not a list")
    ids = [r["hit_id"] for r in rows]
    if len(ids) != len(set(ids)): errs.append("duplicate hit_ids")
    if "--hits" in sys.argv:
        hits = {json.loads(l)["hit_id"] for l in open(sys.argv[sys.argv.index("--hits") + 1])}
        miss = hits - set(ids)
        if miss: errs.append(f"{len(miss)} hits missing from output")
    print(f"rows: {len(rows)}  schema errors: {len(errs)}")
    for e in errs[:20]: print("  ", e)
    if "--score" not in sys.argv: return
    labels = [json.loads(l) for l in open(sys.argv[sys.argv.index("--score") + 1])]
    pred = {r["hit_id"]: r for r in rows}
    cost = base = right = code_ok = cust_ok = ev_exact = 0
    unsafe = []
    conf = {}
    for L in labels:
        p = pred.get(L["hit_id"], {"decision": "REVIEW", "reason_code": "", "customer_record_id": None})
        t, d = L["decision"], p["decision"]
        conf[(t, d)] = conf.get((t, d), 0) + 1
        cost += COST[(t, d)]; base += COST[(t, "REVIEW")]
        if t == d:
            right += 1
            code_ok += p["reason_code"] in lst(L["reason_codes"])
            lc = lst(L["customer_record_ids"])
            cust_ok += (p["customer_record_id"] in lc) or (p["customer_record_id"] is None and not lc)
            ev_exact += set(p.get("evidence") or []) == set(lst(L.get("evidence")))
        if d == "DIFFERENT_ENTITY" and t != "DIFFERENT_ENTITY":
            unsafe.append(L["hit_id"])
    saved = max(0.0, 1 - cost / base) if base else 0
    n = len(labels)
    print(f"labelled hits: {n}")
    print(f"decision accuracy: {right}/{n} = {right/n:.0%}")
    print(f"cost: {cost} vs all-REVIEW baseline {base}  -> decision score (cost saved) {saved:.1%}")
    print(f"unsafe clearances: {len(unsafe)} {unsafe}")
    print(f"of correct decisions: reason code fits {code_ok}/{right}, customer record right {cust_ok}/{right}, "
          f"evidence IDs identical to the reference {ev_exact}/{right}")
    print("confusion (truth -> predicted):")
    for t in ("DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"):
        print(f"  {t:17s}", {d: conf.get((t, d), 0) for d in ("DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW")})

if __name__ == "__main__":
    main()
