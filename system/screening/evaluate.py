"""Score predictions against practice labels the way the organisers do (from the brief).

Decision cost (lower is better):
  truth DIFFERENT_ENTITY: predicted SAME 3, REVIEW 3
  truth SAME_ENTITY:      predicted DIFFERENT 60 (unsafe clearance), REVIEW 2
  truth REVIEW:           predicted DIFFERENT 60 (unsafe clearance), SAME 2
Decision score = share of cost saved versus sending everything to REVIEW (floored at 0).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .contracts import CUSTOMER_AMBIGUOUS, PracticeLabel

COST = {
    ("DIFFERENT_ENTITY", "SAME_ENTITY"): 3,
    ("DIFFERENT_ENTITY", "REVIEW"): 3,
    ("SAME_ENTITY", "DIFFERENT_ENTITY"): 60,
    ("SAME_ENTITY", "REVIEW"): 2,
    ("REVIEW", "DIFFERENT_ENTITY"): 60,
    ("REVIEW", "SAME_ENTITY"): 2,
}


def _cid_ok(pred: str | None, label: PracticeLabel) -> bool:
    accepted = {c.upper() for c in label.customer_record_ids}
    if not accepted or accepted <= {"NULL", "NONE"}:
        return pred is None
    if CUSTOMER_AMBIGUOUS in accepted:
        return (pred or "").upper() == CUSTOMER_AMBIGUOUS
    return (pred or "").upper() in accepted


def score(predictions: list[dict[str, Any]], labels: dict[str, PracticeLabel]) -> dict[str, Any]:
    by_hit = {p["hit_id"]: p for p in predictions}
    cost = baseline = 0
    confusion: Counter[tuple[str, str]] = Counter()
    rows = []
    reason_ok = cid_ok = decided = 0
    ev_tp = ev_pred = ev_true = 0
    for hit_id in sorted(labels):
        label = labels[hit_id]
        truth = label.decision or "REVIEW"
        pred = by_hit.get(hit_id)
        p_dec = pred["decision"] if pred else "MISSING"
        confusion[(truth, p_dec)] += 1
        cost += COST.get((truth, p_dec), 0 if p_dec == truth else 60)
        baseline += COST.get((truth, "REVIEW"), 0)
        correct = p_dec == truth
        r_ok = bool(pred) and pred.get("reason_code") in set(label.reason_codes)
        c_ok = bool(pred) and _cid_ok(pred.get("customer_record_id"), label)
        if correct:
            decided += 1
            reason_ok += r_ok
            cid_ok += c_ok
        pe, te = set(pred.get("evidence", []) if pred else []), set(label.evidence)
        ev_tp, ev_pred, ev_true = ev_tp + len(pe & te), ev_pred + len(pe), ev_true + len(te)
        rows.append({"hit_id": hit_id, "truth": truth, "pred": p_dec, "correct": correct,
                     "reason_ok": r_ok, "customer_ok": c_ok,
                     "unsafe": truth != "DIFFERENT_ENTITY" and p_dec == "DIFFERENT_ENTITY"})
    n = len(labels)
    unsafe = sum(r["unsafe"] for r in rows)
    preds = [r["pred"] for r in rows]
    return {
        "labelled_hits": n,
        "decision_score": round(max(0.0, 1 - cost / baseline), 4) if baseline else None,
        "decision_accuracy": round(decided / n, 4) if n else None,
        "unsafe_clearances": unsafe,
        "review_rate": round(preds.count("REVIEW") / n, 4) if n else None,
        "reason_code_accuracy_on_correct": round(reason_ok / decided, 4) if decided else None,
        "customer_id_accuracy_on_correct": round(cid_ok / decided, 4) if decided else None,
        "evidence_precision": round(ev_tp / ev_pred, 4) if ev_pred else None,
        "evidence_recall": round(ev_tp / ev_true, 4) if ev_true else None,
        "confusion": {f"{t}->{p}": c for (t, p), c in sorted(confusion.items())},
        "rows": rows,
    }
