"""Decision core: the AML Bot reference engine (contrib/aml-bot, PR #28), standard library only.

`aml_engine.py` is copied verbatim from contrib/aml-bot/engine/screen.py so its rules,
Head-of-FinCrime policy calls and independent `second_check` stay exactly as reviewed.
This wrapper only runs it in-process and indexes its decisions by hit id; the rest of
`screening` (modular rules, compiler, triage, explain) treats it as the authoritative
first opinion and adds an independent second check, tiers and a facet trace.

To update the core: re-copy the file from contrib/, then run
`python system/screen.py eval` and `repro --runs 5` (0 unsafe clearances, identical).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import aml_engine as E

CORE_NAME = "aml-core"


def run_core(folder: str | Path) -> dict[str, dict[str, Any]]:
    """Run the core engine on a data folder; return {hit_id: decision dict}.

    Mirrors aml_engine.main() exactly (same per-alert loop, error guards and
    second_check) but returns results instead of writing a file.
    """
    folder = str(folder)
    E.LOAD_WARNINGS.clear()
    try:
        D = E.Data(folder)
    except Exception as exc:  # noqa: BLE001 - mirror main(): still answer every hit
        D = None
        E.LOAD_WARNINGS.append(f"data load failed: {type(exc).__name__}: {exc}")
    out: dict[str, dict[str, Any]] = {}
    alerts = sorted(
        [a for a in (D.alerts if D else []) if a.get("alert_id")], key=lambda a: str(a["alert_id"])
    )
    hits = D.hits if D else {h["hit_id"]: h for h in E.load_jsonl(folder, "hits.jsonl") if h.get("hit_id")}
    by_alert: dict[Any, list[str]] = {}
    for h in hits.values():
        by_alert.setdefault(h.get("alert_id"), []).append(h["hit_id"])
    for alert in alerts:
        ids = list(dict.fromkeys(E.as_list(alert.get("hit_ids")) + sorted(by_alert.get(alert["alert_id"], []))))
        try:
            persons, how = E.resolve_customer(D, alert)
            err = None
        except Exception as exc:  # noqa: BLE001
            persons, how, err = [], "error", f"customer lookup failed: {type(exc).__name__}"
        for hid in ids:
            hit = hits.get(hid)
            if hit is None or hid in out:
                continue
            if err:
                out[hid] = E.error_review(hid, alert["alert_id"], err)
                continue
            try:
                r = E.decide(D, alert, hit, persons, how)
                if r["decision"] == "DIFFERENT_ENTITY":
                    r = E.second_check(D, alert, hit, persons, r)
                out[hid] = r
            except Exception as exc:  # noqa: BLE001
                out[hid] = E.error_review(hid, alert["alert_id"], f"{type(exc).__name__} while deciding")
    for hid in sorted(hits):
        if hid not in out:
            out[hid] = E.error_review(hid, hits[hid].get("alert_id"), "its alert was not found")
    return out


def core_results(ds) -> dict[str, dict[str, Any]]:
    """Core decisions for a loaded Dataset, computed once and cached on it."""
    cached = getattr(ds, "_core_results", None)
    if cached is None:
        cached = run_core(ds.root)
        ds._core_results = cached
    return cached
