"""Dual-control view for the dashboard: decision core vs independent rule check, per hit.

The engine (system/screening/pipeline.py) runs two independent deterministic engines and merges
them (compiler.merge_with_core). The merged HitDecision keeps the core's row as trace[0] but not the
check's own verdict, so `run_dual` records each check verdict as the pipeline computes it (no second
pass, and the decisions are exactly what `screen run` produces).
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable
from typing import Any

from screening import pipeline
from screening.contracts import HitDecision
from screening.loaders import Dataset

ENGINES_CONFLICT = "ENGINES_CONFLICT"
ENGINES_DISAGREE = "ENGINES_DISAGREE"
ESCALATED_BY_POLICY = "ESCALATED_BY_POLICY"
ENGINE_FLAGS = (ENGINES_DISAGREE, ENGINES_CONFLICT, ESCALATED_BY_POLICY)
CORE_RULE = "CORE.aml"
# "Reviewer: <instruction>" up to the end of the reason or the quoted core reason (" Core: ...").
_REVIEWER = re.compile(r"Reviewer:\s*(.+?)(?=\s+Core:\s|$)", re.S)

_RUN_LOCK = threading.Lock()  # run_dual swaps pipeline.check_hit for the duration of a run


def reviewer_action(*reasons: str | None) -> str | None:
    """The human-in-the-loop instruction the engine wrote ("Reviewer: ..."), first one found."""
    for text in reasons:
        m = _REVIEWER.search(text or "")
        if m:
            return m.group(1).strip()
    return None


def run_dual(ds: Dataset, progress: Callable[[str, int, int], None] | None = None
             ) -> tuple[list[HitDecision], dict[str, dict[str, Any]], dict[str, HitDecision]]:
    """Run the engine; return (merged decisions, core results by hit, check decisions by hit)."""
    total = len(ds.hits)
    mode = pipeline.engine_mode()
    core: dict[str, dict[str, Any]] = {}
    if mode in ("combined", "core"):
        if progress:
            progress("decision core", 0, total)
        from screening.core import core_results

        core = core_results(ds)  # cached on ds; pipeline.run reuses it
    checks: dict[str, HitDecision] = {}
    original = pipeline.check_hit

    def recording(ds_: Dataset, hit_id: str) -> HitDecision:
        d = original(ds_, hit_id)
        checks[hit_id] = d
        if progress and len(checks) % 25 == 0:
            progress("independent check", len(checks), total)
        return d

    with _RUN_LOCK:
        pipeline.check_hit = recording
        try:
            if progress:
                progress("independent check", 0, total)
            results = pipeline.run(ds, mode)
        finally:
            pipeline.check_hit = original
    return results, core, checks


def _core_view(core: dict[str, Any] | None) -> dict[str, Any] | None:
    if not core:
        return None
    return {
        "decision": core.get("decision"),
        "reason_code": core.get("reason_code"),
        "reason": core.get("reason") or "",
        "evidence": [str(e) for e in core.get("evidence") or () if e],
        "customer_record_id": core.get("customer_record_id"),
    }


def _check_view(check: HitDecision | None) -> dict[str, Any] | None:
    if check is None:
        return None
    return {
        "decision": check.decision.value,
        "reason_code": check.reason_code.value,
        "reason": check.reason,
        "evidence": list(check.evidence),
        "customer_record_id": check.customer_record_id,
        "tier": check.tier.value,
        "flags": list(check.flags),
    }


def agreement(core_decision: str | None, check_decision: str | None, flags: list[str]) -> str:
    """AGREE / DISAGREE / CONFLICT (core clears, check confirms) / UNAVAILABLE (one engine missing)."""
    if ENGINES_CONFLICT in flags:
        return "CONFLICT"
    if not core_decision or not check_decision:
        return "UNAVAILABLE"
    if core_decision == check_decision:
        return "AGREE"
    if core_decision == "DIFFERENT_ENTITY" and check_decision == "SAME_ENTITY":
        return "CONFLICT"
    return "DISAGREE"


def engine_view(full: dict[str, Any], core: dict[str, Any] | None, check: HitDecision | None) -> dict[str, Any]:
    """Per-hit dual-control block for the API (`decision.engines`)."""
    c, k = _core_view(core), _check_view(check)
    flags = list(full.get("flags") or [])
    status = agreement(c["decision"] if c else None, k["decision"] if k else None, flags)
    return {
        "core": c,
        "check": k,
        "agreement": status,
        "escalated_by_policy": ESCALATED_BY_POLICY in flags,
        "reviewer_action": reviewer_action(full.get("reason"), c["reason"] if c else None),
    }


def engine_summary(views: list[dict[str, Any]]) -> dict[str, Any]:
    status = Counter(v["agreement"] for v in views)
    compared = sum(n for s, n in status.items() if s != "UNAVAILABLE")
    pairs = Counter(
        f"{v['core']['decision'] if v['core'] else 'NONE'}/{v['check']['decision'] if v['check'] else 'NONE'}"
        for v in views
    )
    return {
        "agreement": dict(sorted(status.items())),
        "compared": compared,
        "agreement_rate": round(status.get("AGREE", 0) / compared, 4) if compared else None,
        "escalated_by_policy": sum(1 for v in views if v["escalated_by_policy"]),
        "with_reviewer_action": sum(1 for v in views if v["reviewer_action"]),
        "pairs": dict(sorted(pairs.items())),
        "mode": pipeline.engine_mode(),
    }
