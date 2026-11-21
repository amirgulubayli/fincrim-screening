"""Command line for people, agents and bots (dashboard terminal, Grok plugin). Standard library only.

  screen run DATA OUT [--trace FILE]         decide every hit; OUT = submission JSONL; prints tiers +
                                             engine agreement counts
  screen explain HIT_ID --data DATA          final decision, core vs independent check, facet trace,
                                             agreement, customer resolution path, evidence, policies
  screen eval --data DATA [--pred F]         score against practice_labels.jsonl
  screen eval --data DATA --compare          score core, check and combined side by side
  screen repro --data DATA --runs 5          run N times in fresh processes, prove identical
  screen summary --data DATA                 dataset + decision counts by HITL tier
  screen diff --data DATA [--pair C:K]       where the decision core and the independent check disagree
  screen queue --tier TIER --data DATA       the analyst queue for one tier, one-line why per hit
  screen policy [ID ...]                     the policy registry (SOP P1-P9, HOFC-1..9, DC-1..3)
  screen stress --data DATA [--kinds K ...]  contrib/aml-bot perturbation stress test against OUR engine

--engine core|check|combined (run/eval/explain/repro/diff/queue/stress) selects the engine; default is
$SCREEN_ENGINE or combined. Add --json to any command for machine-readable output.

Colour: on a terminal, or with SCREEN_COLOR=1 / FORCE_COLOR set, output is coloured (case cards, tier badges,
live progress on stderr). SCREEN_COLOR=0 or NO_COLOR turns it off. --json output and the files `run` writes
never change.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from . import pipeline as _pipeline
from .compiler import ENGINES_CONFLICT, ENGINES_DISAGREE, merge_with_core
from .contracts import REASONS_BY_DECISION, Decision, HitDecision, ReasonCode, Signal, Tier
from .evaluate import score
from .loaders import load_dataset
from .pipeline import ENGINES, check_hit, engine_mode, fallback, run
from .policy import BY_ID, REGISTRY, policies_for
from .resolve import resolve_customer

SCREEN_PY = Path(__file__).resolve().parent.parent / "screen.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
STRESS_PY = REPO_ROOT / "contrib" / "aml-bot" / "engine" / "stress_test.py"
TIERS = tuple(t.value for t in Tier)
DECISION_ABBR = {"D": "DIFFERENT_ENTITY", "DIFF": "DIFFERENT_ENTITY", "DIFFERENT": "DIFFERENT_ENTITY",
                 "S": "SAME_ENTITY", "SAME": "SAME_ENTITY", "R": "REVIEW", "REV": "REVIEW"}


def _dump(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        for key, value in obj.items():
            print(f"{key:>34}: {value}")


def _mode(args: argparse.Namespace) -> str:
    return getattr(args, "engine", None) or engine_mode()


# --------------------------------------------------------------------------- shared helpers

_REVIEWER_RE = re.compile(r"Reviewer:\s*(.+?\.)(?=\s|$)", re.S)


def reviewer_action(reason: str | None) -> str | None:
    """The 'Reviewer: ...' guidance sentence of an 'Escalated, not cleared' reason, if any."""
    m = _REVIEWER_RE.search(reason or "")
    return m.group(1).strip() if m else None


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    m = re.search(r"(?<=[a-z0-9)\]'])\.\s", text)
    s = text[: m.start() + 1] if m else text
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def core_decision(core: dict[str, Any] | None) -> Decision | None:
    """The core's decision as merge_with_core reads it (invalid values / codes -> REVIEW)."""
    if core is None:
        return None
    try:
        decision = Decision(core.get("decision"))
    except ValueError:
        return Decision.REVIEW
    try:
        code = ReasonCode(core.get("reason_code"))
    except ValueError:
        return Decision.REVIEW
    return decision if code in REASONS_BY_DECISION[decision] else Decision.REVIEW


def agreement(core: dict[str, Any] | None, check: HitDecision | None) -> str:
    """AGREE / DISAGREE / CONFLICT (core clears, check confirms) / N/A (an engine missing)."""
    c = core_decision(core)
    if c is None or check is None:
        return "N/A"
    if c is check.decision:
        return "AGREE"
    if c is Decision.DIFFERENT_ENTITY and check.decision is Decision.SAME_ENTITY:
        return "CONFLICT"
    return "DISAGREE"


def _core_for_hit(ds, hit_id: str) -> dict[str, Any] | None:
    """The core's decision for ONE hit without deciding all the others (fast `explain`).

    Mirrors core.run_core exactly for that hit: the first alert (sorted by id) that lists it decides it,
    with the same error guards and second_check. Falls back to the full run if the core cannot load.
    """
    from .core import core_results

    cached = getattr(ds, "_core_results", None)
    if cached is not None:
        return cached.get(hit_id)
    from .core import aml_engine as E

    E.LOAD_WARNINGS.clear()
    try:
        D = E.Data(str(ds.root))
    except Exception:  # noqa: BLE001 - same answer as the full run, just slower
        return core_results(ds).get(hit_id)
    hits = D.hits
    hit = hits.get(hit_id)
    if hit is None:
        return None
    for alert in sorted([a for a in D.alerts if a.get("alert_id")], key=lambda a: str(a["alert_id"])):
        listed = E.as_list(alert.get("hit_ids"))
        if hit_id not in listed and hit.get("alert_id") != alert["alert_id"]:
            continue
        try:
            persons, how = E.resolve_customer(D, alert)
        except Exception as exc:  # noqa: BLE001
            return E.error_review(hit_id, alert["alert_id"], f"customer lookup failed: {type(exc).__name__}")
        try:
            r = E.decide(D, alert, hit, persons, how)
            if r["decision"] == "DIFFERENT_ENTITY":
                r = E.second_check(D, alert, hit, persons, r)
            return r
        except Exception as exc:  # noqa: BLE001
            return E.error_review(hit_id, alert["alert_id"], f"{type(exc).__name__} while deciding")
    return E.error_review(hit_id, hit.get("alert_id"), "its alert was not found")


def _safe_check(ds, hit_id: str) -> tuple[HitDecision, bool]:
    """(check decision, errored). An erroring check becomes the pipeline's CHECK_ERROR fallback."""
    try:
        return check_hit(ds, hit_id), False
    except Exception as exc:  # noqa: BLE001
        return fallback(hit_id, ds.hits[hit_id].alert_id, f"CHECK_ERROR:{type(exc).__name__}"), True


def _final(ds, hit_id: str, mode: str, core: dict[str, Any] | None, check: HitDecision,
           check_err: bool) -> HitDecision:
    """Same result as pipeline.run(ds, mode) for this hit, reusing already computed engine outputs."""
    alert_id = ds.hits[hit_id].alert_id
    try:
        if mode == "check":
            if check_err:
                return fallback(hit_id, alert_id, check.flags[0].replace("CHECK_ERROR", "ENGINE_ERROR"))
            return check
        return merge_with_core(core, None if mode == "core" else check, hit_id, alert_id)
    except Exception as exc:  # noqa: BLE001
        return fallback(hit_id, alert_id, f"ENGINE_ERROR:{type(exc).__name__}")


def _vs_core(signal: Signal, core: Decision | None) -> str:
    if signal is Signal.NEUTRAL or core in (None, Decision.REVIEW):
        return "neutral"
    supports = (signal is Signal.CONTRADICTION) == (core is Decision.DIFFERENT_ENTITY)
    return "supports" if supports else "contradicts"


# --------------------------------------------------------------------------- presentation (ANSI colour)
#
# Only the human-readable output is styled. --json output and the files `run` writes are never touched, and
# without SCREEN_COLOR / FORCE_COLOR / a TTY the plain text below each command is exactly what it always was.

BOLD, DIM, ITALIC = "1", "2", "3"
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, GREY = "31", "32", "33", "34", "35", "36", "90"
BLACK, WHITE = "30", "97"
BG_RED, BG_GREEN, BG_YELLOW, BG_BLUE = "41", "42", "43", "44"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ID_RE = re.compile(r"\b[A-Z]{1,5}-[0-9A-Z]{4,}\b")
_OFF = {"0", "false", "no", "off", "never"}

VERDICT = {  # decision -> (headline, colour)
    "DIFFERENT_ENTITY": ("CLEARED", GREEN),
    "SAME_ENTITY": ("MATCH → compliance", RED),
    "REVIEW": ("REVIEW → analyst", YELLOW),
}
DECISION_COLOR = {"DIFFERENT_ENTITY": GREEN, "SAME_ENTITY": RED, "REVIEW": YELLOW}
TIER_STYLE = {
    "AUTO_CLEAR": (BLACK, BG_GREEN),
    "AUTO_ESCALATE": (WHITE, BG_RED),
    "BORDERLINE": (BLACK, BG_YELLOW),
    "HUMAN_REVIEW": (WHITE, BG_BLUE),
}
TIER_MEANING = {
    "AUTO_CLEAR": "cleared on a hard contradiction",
    "AUTO_ESCALATE": "straight to compliance",
    "BORDERLINE": "4-eyes: a second analyst confirms",
    "HUMAN_REVIEW": "analyst decides",
}
AGREEMENT_STYLE = {"AGREE": ("✓ AGREE", GREEN), "DISAGREE": ("≠ DISAGREE", YELLOW),
                   "CONFLICT": ("✗ CONFLICT", RED), "N/A": ("- n/a", GREY)}
SIGNAL_ICON = {"agreement": ("✓", GREEN), "contradiction": ("✗", RED), "neutral": ("·", GREY)}
VS_CORE_COLOR = {"supports": GREEN, "contradicts": RED, "neutral": GREY}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _enable_vt() -> bool:
    """Windows consoles need virtual-terminal processing switched on for ANSI codes."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ok = True
        for std in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ok &= bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        return ok
    except Exception:  # noqa: BLE001 - no console API: just no colour
        return False


def color_enabled(stream: Any = None) -> bool:
    """SCREEN_COLOR=1 / FORCE_COLOR force colour; SCREEN_COLOR=0 / NO_COLOR turn it off; else only on a TTY."""
    forced = os.environ.get("SCREEN_COLOR", "").strip().lower()
    if forced:
        return forced not in _OFF
    if "FORCE_COLOR" in os.environ:
        return os.environ["FORCE_COLOR"].strip().lower() not in _OFF
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty()) and _enable_vt()
    except (AttributeError, ValueError):
        return False


def _width() -> int:
    try:
        return max(60, min(160, int(os.environ.get("SCREEN_WIDTH") or 100)))
    except ValueError:
        return 100


class Style:
    """Wraps text in SGR codes when on; identity when off (so one renderer serves both)."""

    def __init__(self, on: bool):
        self.on = on

    def __call__(self, text: Any, *codes: str) -> str:
        text = str(text)
        if not self.on or not codes or not text:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"

    def ids(self, text: str, *codes: str) -> str:
        """Style `text`, highlighting record IDs (KYC-..., CUS-..., NOTE-...) in cyan."""
        out, pos = [], 0
        for m in _ID_RE.finditer(text):
            out.append(self(text[pos:m.start()], *codes))
            out.append(self(m.group(), BOLD, CYAN) if BOLD in codes else self(m.group(), CYAN))
            pos = m.end()
        out.append(self(text[pos:], *codes))
        return "".join(out)

    def num(self, value: Any) -> str:
        return self(value, BOLD, CYAN)

    def decision(self, value: str | None) -> str:
        return self(value or "-", BOLD, DECISION_COLOR.get(value or "", GREY))

    def verdict(self, decision: str | None) -> str:
        label, color = VERDICT.get(decision or "", (decision or "-", GREY))
        return self(f"● {label}", BOLD, color)

    def badge(self, tier: str | None, pad: int = 0) -> str:
        fg, bg = TIER_STYLE.get(tier or "", (WHITE, "100"))
        text = f" {tier or '-'} ".ljust(pad + 2) if pad else f" {tier or '-'} "
        return self(text, BOLD, fg, bg)

    def agreement(self, value: str | None) -> str:
        label, color = AGREEMENT_STYLE.get(value or "N/A", (value or "-", GREY))
        return self(label, BOLD, color)

    def flags(self, flags: list[str] | tuple[str, ...]) -> str:
        return " ".join(self(f, YELLOW) for f in flags) if flags else self("-", GREY)


def _style(args: argparse.Namespace) -> Style:
    return Style(color_enabled() and not getattr(args, "json", False))


def _section(s: Style, title: str, width: int, extra: str = "") -> str:
    head = f" {s(title, BOLD, CYAN)}" + (f"  {extra}" if extra else "")
    pad = max(3, width - len(strip_ansi(head)) - 1)
    return f"{head} {s(chr(0x2500) * pad, GREY)}"


def _wrap(text: Any, avail: int) -> list[str]:
    return textwrap.wrap(" ".join(str(text or "").split()), max(20, avail),
                         break_on_hyphens=False, break_long_words=False) or ["-"]


def _block(s: Style, text: Any, width: int, first: str, rest: str | None = None, *codes: str) -> list[str]:
    """Wrap `text` under a (possibly styled) prefix; continuation lines get `rest` (default: blank pad)."""
    rest = " " * len(strip_ansi(first)) if rest is None else rest
    avail = width - len(strip_ansi(first))
    return [(first if i == 0 else rest) + s.ids(chunk, *codes) for i, chunk in enumerate(_wrap(text, avail))]


def _bar(s: Style, frac: float, width: int = 20, color: str = GREEN) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return s("█" * full, color) + s("░" * (width - full), GREY)


def render_explain(x: dict[str, Any], color: bool = True, width: int | None = None) -> str:
    """The case card for one hit (the dict from explain_hit): verdict, why, reviewer action, core vs check,
    the facet-by-facet trace, the customer resolution path, evidence and policies."""
    s = Style(color)
    w = width or _width()
    out: list[str] = []
    left = f" {s(x['hit_id'], BOLD)}  {s('alert', GREY)} {s(x['alert_id'], CYAN)}"
    right = f"{s('engine', GREY)} {s(x['engine'], BOLD)}"
    out.append(left + " " * max(2, w - len(strip_ansi(left)) - len(strip_ansi(right))) + right)
    out.append(" " + s("═" * (w - 1), GREY))
    out.append(f"   {s.verdict(x['decision'])}   {s.decision(x['decision'])} {s('/', GREY)} "
               f"{s(x['reason_code'], BOLD)}   {s.badge(x['tier'])}   {s('engines', GREY)} "
               f"{s.agreement(x['agreement'])}")
    out.append(f"   {s('customer', GREY)}  {s.ids(str(x['customer_record_id'] or '-'), BOLD)}"
               f"   {s('flags', GREY)}  {s.flags(x['flags'])}"
               f"   {s('tier', GREY)}  {s(TIER_MEANING.get(x['tier'], ''), ITALIC)}")
    out.append("")
    out.append(_section(s, "WHY", w))
    out += _block(s, x["reason"], w, "   ")
    if x.get("reviewer_action"):
        out.append("")
        out += _block(s, x["reviewer_action"], w, f"   {s(' REVIEWER ACTION ', BOLD, BLACK, BG_YELLOW)}  ",
                      None, BOLD, YELLOW)
    out.append("")
    out.append(_section(s, "CORE vs CHECK", w, s("two independent deterministic engines", GREY)))
    core = x.get("core")
    if core:
        tag = f"   {s('escalated by policy', BOLD, MAGENTA)}" if core["escalated_by_policy"] else ""
        out.append(f"   {s('core ', BOLD)}  {s.decision(core['decision'])} {s('/', GREY)} {core['reason_code']}{tag}")
        if core.get("reason") and core["reason"] != x["reason"]:
            out += _block(s, core["reason"], w, "          ", None, GREY)
        elif core.get("reason"):
            out.append(f"          {s('(same reason as above)', GREY)}")
        if core["reviewer_action"] and core["reviewer_action"] != x["reviewer_action"]:
            out += _block(s, core["reviewer_action"], w, f"          {s('reviewer:', YELLOW)} ")
    else:
        out.append(f"   {s('core ', BOLD)}  {s('(no answer)', GREY)}")
    chk = x["check"]
    err = f"   {s('check errored', BOLD, RED)}" if chk.get("errored") else ""
    out.append(f"   {s('check', BOLD)}  {s.decision(chk['decision'])} {s('/', GREY)} {chk['reason_code']}"
               f"   {s('flags', GREY)} {s.flags(chk['flags'])}{err}")
    out.append("")
    legend = s("✓ agrees  ✗ contradicts  · neutral   |  vs core", GREY)
    out.append(_section(s, "RULE TRACE", w, legend))
    facets = chk.get("facets") or []
    for i, f in enumerate(facets):
        last = i == len(facets) - 1
        rail, cont = ("└─", "  ") if last else ("├─", "│ ")
        icon, icolor = SIGNAL_ICON.get(f["signal"], ("?", GREY))
        vs = f.get("vs_core", "neutral")
        head = (f"   {s(rail, GREY)} {s(icon, BOLD, icolor)} {s(str(f['rule_id']).ljust(12), BOLD)} "
                f"{s(str(f['signal']).ljust(13), icolor)} {s(vs.ljust(11), VS_CORE_COLOR.get(vs, GREY))} ")
        summary = f["summary"] + (f"  [WEAK: {f['weak_why']}]" if f.get("weak") else "")
        out += _block(s, summary, w, head, f"   {s(cont, GREY)}" + " " * (len(strip_ansi(head)) - 5))
    if not facets:
        out.append(f"   {s('(no facets: the check could not evaluate this hit)', GREY)}")
    res = x.get("resolution")
    if res:
        persons = ", ".join("+".join(p["record_ids"]) for p in res["persons"]) or "none"
        out.append("")
        out.append(_section(s, "RESOLUTION PATH", w,
                            f"{s(res['method'], BOLD)} {s(chr(0x2192), GREY)} {s.ids(persons, BOLD)}"))
        for n, step in enumerate(res["path"], 1):
            out += _block(s, step.replace(" -> ", " → "), w, f"   {s(f'{n}.', GREY)} ")
    out.append("")
    ev = x.get("evidence") or []
    out.append(_section(s, "EVIDENCE", w, "   ".join(s(e, BOLD, CYAN) for e in ev) if ev else s("none", GREY)))
    titles = x.get("policy_titles") or {}
    out.append(_section(s, "POLICIES", w))
    for pid, title in titles.items():
        out.append(f"   {s(f'{pid:<7}', BOLD, MAGENTA)} {title}")
    if not titles:
        out.append(f"   {s('-', GREY)}")
    return "\n".join(out)


def explain_text(ds, hit_id: str, mode: str | None = None, color: bool = True, width: int | None = None) -> str:
    """Render the case card for a hit of an already loaded Dataset (the dashboard reuses its loaded run)."""
    return render_explain(explain_hit(ds, hit_id, mode or engine_mode()), color=color, width=width)


def _value(s: Style, v: Any) -> str:
    if isinstance(v, bool):
        return s("✓ true", BOLD, GREEN) if v else s("✗ false", BOLD, RED)
    if v is None:
        return s("-", GREY)
    if isinstance(v, (int, float)):
        return s.num(v)
    if isinstance(v, dict):
        return s("  ·  ", GREY).join(f"{s(k, GREY)} {_value(s, val)}" for k, val in v.items()) or s("-", GREY)
    if isinstance(v, (list, tuple)):
        if v and all(isinstance(i, dict) for i in v):
            return s(f"{len(v)} rows", GREY)
        return ", ".join(s.ids(str(i)) for i in v) or s("-", GREY)
    return s.ids(str(v))


def _print_kv(s: Style, obj: dict[str, Any], key_width: int = 22) -> None:
    for key, value in obj.items():
        print(f" {s(f'{key:>{key_width}}', GREY)}  {_value(s, value)}")


class _Progress:
    """A live progress line on stderr (spinner, stage, bar, hits processed / total, elapsed)."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, s: Style, stream: Any = None):
        self.s, self.stream = s, stream or sys.stderr
        self.label, self.done, self.total = "starting", 0, 0
        self.t0 = time.perf_counter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _Progress:
        self._thread = threading.Thread(target=self._spin, name="screen-progress", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._write("\r\x1b[2K")

    def stage(self, label: str, total: int = 0) -> None:
        self.label, self.done, self.total = label, 0, total

    def tick(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            pass

    def _spin(self) -> None:
        i = 0
        while not self._stop.wait(0.1):
            i += 1
            s = self.s
            frame = s(self.FRAMES[i % len(self.FRAMES)], BOLD, CYAN)
            elapsed = s(f"{time.perf_counter() - self.t0:6.1f}s", GREY)
            if self.total:
                frac = self.done / self.total
                line = (f"  {frame} {s(f'{self.label:<20}', BOLD)} {_bar(s, frac, 28, CYAN)} "
                        f"{s.num(f'{self.done:>4}')}{s(f'/{self.total} hits', GREY)}  {elapsed}")
            else:
                line = f"  {frame} {s(f'{self.label:<20}', BOLD)} {s('working ...', GREY)}  {elapsed}"
            self._write("\r\x1b[2K" + line)


class _NoProgress:
    """Stand-in when colour is off: same interface, no output."""

    def __enter__(self) -> _NoProgress:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def stage(self, label: str, total: int = 0) -> None:
        return None

    def tick(self, done: int, total: int) -> None:
        return None


def _live(s: Style) -> _Progress | _NoProgress:
    return _Progress(s) if s.on else _NoProgress()


def _decide(ds, mode: str, progress: _Progress | _NoProgress | None, prefix: str = "") -> list[HitDecision]:
    """pipeline.run(ds, mode), reporting hits processed / total to `progress` (same decisions either way)."""
    if not isinstance(progress, _Progress):
        return run(ds, mode)
    total = len(ds.hits)
    if mode != "check":
        from .core import core_results

        progress.stage(f"{prefix}decision core")
        try:
            core_results(ds)  # cached on ds; run() reuses it (an error here resurfaces per hit inside run())
        except Exception:  # noqa: BLE001
            pass
    progress.stage(f"{prefix}{'merging' if mode == 'core' else 'independent check'}", total)
    original, count = _pipeline.adjudicate, [0]

    def counted(*a: Any, **k: Any) -> HitDecision:
        try:
            return original(*a, **k)
        finally:
            count[0] += 1
            progress.tick(count[0], total)

    _pipeline.adjudicate = counted
    try:
        return run(ds, mode)
    finally:
        _pipeline.adjudicate = original


def _render_run(s: Style, summary: dict[str, Any], out_path: Path) -> None:
    w = _width()
    total = summary["hits"] or 1
    print(_section(s, "RUN", w, f"{s('engine', GREY)} {s(summary['engine'], BOLD)}  {s('|', GREY)}  "
                                f"{s.num(summary['hits'])} {s('hits', GREY)}  {s('|', GREY)}  "
                                f"{s.num(summary['seconds'])} {s('s', GREY)}"))
    print(f"   {s('TIER'.ljust(15), GREY)} {s('HITS'.rjust(6), GREY)} {s('SHARE'.rjust(7), GREY)}")
    tiers = summary["tiers"]
    for tier in TIERS:
        n = tiers.get(tier, 0)
        fg, bg = TIER_STYLE[tier]
        print(f"   {s.badge(tier, 13)} {s.num(f'{n:>6}')} {s(f'{100 * n / total:6.1f}%', GREY)}  "
              f"{_bar(s, n / total, 30, {BG_GREEN: GREEN, BG_RED: RED, BG_YELLOW: YELLOW}.get(bg, BLUE))}  "
              f"{s(TIER_MEANING[tier], GREY)}")
    print(f"   {s(chr(0x2500) * 15, GREY)} {s(chr(0x2500) * 6, GREY)}")
    print(f"   {s('total'.ljust(15), BOLD)} {s.num(str(summary['hits']).rjust(6))}")
    print("")
    decs = summary["decisions"]
    print(f"   {s('decisions', GREY)}  " + s("  ·  ", GREY).join(
        f"{s.decision(k)} {s.num(v)}" for k, v in decs.items()))
    agree = summary.get("engine_agreement")
    if agree:
        print(f"   {s('engines  ', GREY)}  {s('agree', GREEN)} {s.num(agree['agree'])}  ·  "
              f"{s('disagree', YELLOW)} {s.num(agree['disagree'])} {s('(4-eyes)', GREY)}  ·  "
              f"{s('conflict', RED)} {s.num(agree['conflict'])}")
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest() if out_path.is_file() else "-"
    print(f"   {s('output   ', GREY)}  {s(summary['output'], BOLD)}   {s('sha256', GREY)} {s(digest[:16], BOLD, CYAN)}"
          f"{s(digest[16:], GREY)}")


def _short(pair: str) -> str:
    """DIFFERENT_ENTITY->REVIEW -> DIFFERENT->REVIEW (confusion keys on one line)."""
    return pair.replace("DIFFERENT_ENTITY", "DIFFERENT").replace("SAME_ENTITY", "SAME")


def _metric_cell(s: Style, key: str, value: Any, width: int) -> str:
    text = f"{str(value):>{width}}"
    if key == "unsafe_clearances":
        return s(text, BOLD, GREEN if not value else RED)
    if isinstance(value, (int, float)):
        return s.num(text)
    return s(text, GREY)


def _render_eval(s: Style, result: dict[str, Any], mode: str) -> None:
    w = _width()
    print(_section(s, "SCORECARD", w, f"{s('engine', GREY)} {s(mode, BOLD)}  {s('|', GREY)}  "
                                      f"{s.num(result.get('labelled_hits'))} {s('practice labels', GREY)}"))
    for key, value in result.items():
        if key in ("labelled_hits", "confusion"):
            continue
        label = s(f"{key.replace('_', ' '):<32}", GREY)
        extra = ""
        if key == "decision_score" and isinstance(value, (int, float)):
            extra = "  " + _bar(s, value, 30, GREEN)
        elif key == "unsafe_clearances":
            extra = ("  " + s("✓ no real match was ever cleared", GREEN)) if not value else \
                ("  " + s(f"✗ {value} UNSAFE CLEARANCE(S)", BOLD, RED))
        print(f"   {label} {_metric_cell(s, key, value, 8)}{extra}")
    conf = result.get("confusion") or {}
    if conf:
        print(f"   {s('confusion (truth->pred)'.ljust(32), GREY)} " + s("  ·  ", GREY).join(
            f"{s(_short(k), GREY if k.split('->')[0] == k.split('->')[-1] else YELLOW)} {s.num(v)}" for k, v in conf.items()))


def _render_compare(s: Style, engines: dict[str, dict[str, Any]], mode: str) -> None:
    w = _width()
    n = engines["combined"].get("labelled_hits")
    print(_section(s, "SCORECARD", w, f"{s('core vs check vs combined', BOLD)}  {s('|', GREY)}  {s.num(n)} "
                                      f"{s('practice labels', GREY)}  {s('|', GREY)}  {s('selected', GREY)} "
                                      f"{s(mode, BOLD)}"))
    head = "".join(s(f"{('> ' if m == mode else '') + m:>12}", BOLD if m == mode else GREY) for m in engines)
    print(f"   {s('metric'.ljust(32), GREY)}{head}")
    for key in engines["combined"]:
        if key in ("confusion", "labelled_hits"):
            continue
        cells = "".join(_metric_cell(s, key, engines[m][key], 12) for m in engines)
        print(f"   {s(key.replace('_', ' ').ljust(32), GREY)}{cells}")
    unsafe = engines[mode]["unsafe_clearances"]
    print("")
    print(f"   {s('verdict', GREY)}  " + (s(f"✓ 0 unsafe clearances with the {mode} engine", BOLD, GREEN)
                                          if not unsafe else s(f"✗ {unsafe} unsafe clearance(s)", BOLD, RED)))
    for m, r in engines.items():
        print(f"   {s(f'confusion {m:<9}', GREY)} " + s("  ·  ", GREY).join(
            f"{s(_short(k), GREY if k.split('->')[0] == k.split('->')[-1] else YELLOW)} {s.num(v)}"
            for k, v in r["confusion"].items()))


# --------------------------------------------------------------------------- commands


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    mode = _mode(args)
    s = _style(args)
    live = s.on and not args.quiet
    with (_Progress(s) if live else _NoProgress()) as progress:
        progress.stage("loading data")
        ds = load_dataset(args.data)
        decisions = _decide(ds, mode, progress)
        progress.stage("writing output")
        _dump([d.submission() for d in decisions], Path(args.out))
    if args.trace:
        _dump([d.full() for d in decisions], Path(args.trace))
    summary = {
        "engine_version": __version__,
        "engine": mode,
        "hits": len(decisions),
        "decisions": dict(sorted(Counter(d.decision.value for d in decisions).items())),
        "tiers": dict(sorted(Counter(d.tier.value for d in decisions).items())),
        "engine_agreement": _agreement_counts(decisions, mode),
        "seconds": round(time.perf_counter() - started, 2),
        "output": str(args.out),
    }
    if live:
        _render_run(s, summary, Path(args.out))
    elif not args.quiet:
        _print(summary, args.json)
    return 0


def _agreement_counts(decisions: list[HitDecision], mode: str) -> dict[str, int] | None:
    """From the merge flags; only the combined engine compares the two engines."""
    if mode != "combined":
        return None
    conflict = sum(ENGINES_CONFLICT in d.flags for d in decisions)
    disagree = sum(ENGINES_DISAGREE in d.flags for d in decisions)
    return {"agree": len(decisions) - conflict - disagree, "disagree": disagree, "conflict": conflict}


def explain_hit(ds, hit_id: str, mode: str) -> dict[str, Any]:
    """Everything an analyst (or the dashboard / Grok plugin) needs about one hit."""
    core = _core_for_hit(ds, hit_id)
    check, check_err = _safe_check(ds, hit_id)
    final = _final(ds, hit_id, mode, core, check, check_err)
    cdec = core_decision(core)
    out = final.full()  # backward compatible top-level keys
    out["engine"] = mode
    out["reviewer_action"] = reviewer_action(final.reason)
    out["core"] = None if core is None else {
        "decision": core.get("decision"),
        "reason_code": core.get("reason_code"),
        "customer_record_id": core.get("customer_record_id"),
        "reason": core.get("reason"),
        "reviewer_action": reviewer_action(core.get("reason")),
        "escalated_by_policy": str(core.get("reason") or "").startswith("Escalated, not cleared"),
        "evidence": [str(e) for e in core.get("evidence") or () if e],
    }
    out["check"] = {
        "decision": check.decision.value,
        "reason_code": check.reason_code.value,
        "customer_record_id": check.customer_record_id,
        "reason": check.reason,
        "tier": check.tier.value,
        "flags": list(check.flags),
        "errored": check_err,
        "facets": [{**r.to_dict(), "vs_core": _vs_core(r.signal, cdec)} for r in check.trace],
    }
    out["agreement"] = agreement(core, None if check_err else check)
    alert = ds.alerts.get(ds.hits[hit_id].alert_id)
    if alert is None:
        out["resolution"] = None
    else:
        res = resolve_customer(ds, alert)
        out["resolution"] = {
            "method": res.method,
            "path": list(res.path),
            "candidates": list(res.candidates),
            "persons": [{"key": p.key, "record_ids": list(p.record_ids)} for p in res.persons],
        }
    out["policies"] = policies_for(final)
    out["policy_titles"] = {pid: BY_ID[pid].title for pid in out["policies"]}
    return out


def cmd_explain(args: argparse.Namespace) -> int:
    s = _style(args)
    with _live(s) as progress:
        progress.stage("loading data")
        ds = load_dataset(args.data)
        if args.hit_id not in ds.hits:
            x = None
        else:
            progress.stage(f"explaining {args.hit_id}")
            x = explain_hit(ds, args.hit_id, _mode(args))
    if x is None:
        print(f"unknown hit {args.hit_id}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(x, indent=2, ensure_ascii=False))
        return 0
    if s.on:
        print(render_explain(x, color=True))
        return 0
    print(f"{x['hit_id']} (alert {x['alert_id']}) -> {x['decision']} / {x['reason_code']}   [engine {x['engine']}]")
    print(f"  tier: {x['tier']}   flags: {', '.join(x['flags']) or '-'}   agreement: {x['agreement']}")
    print(f"  customer: {x['customer_record_id']}   evidence: {', '.join(x['evidence']) or '-'}")
    print(f"  reason: {x['reason']}")
    if x["reviewer_action"]:
        print(f"  REVIEWER ACTION: {x['reviewer_action']}")
    core = x["core"]
    if core:
        print(f"  core:  {core['decision']} / {core['reason_code']}"
              f"{'   (escalated by policy)' if core['escalated_by_policy'] else ''}")
        print(f"         {core['reason']}")
        if core["reviewer_action"] and core["reviewer_action"] != x["reviewer_action"]:
            print(f"         reviewer: {core['reviewer_action']}")
    else:
        print("  core:  (no answer)")
    chk = x["check"]
    print(f"  check: {chk['decision']} / {chk['reason_code']}   flags: {', '.join(chk['flags']) or '-'}")
    for f in chk["facets"]:
        weak = f" [WEAK: {f['weak_why']}]" if f["weak"] else ""
        print(f"    {f['rule_id']:<12} {f['signal']:<13} {f['vs_core']:<11} {f['summary']}{weak}")
    res = x["resolution"]
    if res:
        persons = ", ".join("+".join(p["record_ids"]) for p in res["persons"]) or "none"
        print(f"  resolution: {res['method']} -> {persons}")
        for step in res["path"]:
            print(f"    - {step}")
    print("  policies: " + (", ".join(f"{p} ({t})" for p, t in x["policy_titles"].items()) or "-"))
    return 0


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "rows"}


def cmd_eval(args: argparse.Namespace) -> int:
    s = _style(args)
    with _live(s) as progress:
        progress.stage("loading data")
        ds = load_dataset(args.data)
    if not ds.practice_labels:
        print("no practice_labels.jsonl in the data folder", file=sys.stderr)
        return 2
    mode = _mode(args)
    if args.compare:
        with _live(s) as progress:
            engines = {m: _metrics(score([d.submission() for d in _decide(ds, m, progress, f"{m}: ")],
                                         ds.practice_labels))
                       for m in ("core", "check", "combined")}
        if args.json:
            print(json.dumps({"selected": mode, "engines": engines}, indent=2, ensure_ascii=False))
        elif s.on:
            _render_compare(s, engines, mode)
        else:
            print(f"{'metric':>34}  {'core':>12} {'check':>12} {'combined':>12}")
            for key in engines["combined"]:
                if key == "confusion":
                    continue
                print(f"{key:>34}  " + " ".join(f"{str(engines[m][key]):>12}" for m in engines))
            for m, r in engines.items():
                print(f"{'confusion ' + m:>34}  {r['confusion']}")
        return 1 if engines[mode]["unsafe_clearances"] else 0
    if args.pred:
        with open(args.pred, encoding="utf-8") as fh:
            preds = [json.loads(line) for line in fh if line.strip()]
    else:
        with _live(s) as progress:
            preds = [d.submission() for d in _decide(ds, mode, progress)]
    result = score(preds, ds.practice_labels)
    rows = result.pop("rows")
    if s.on:
        _render_eval(s, result, "file " + str(args.pred) if args.pred else mode)
    else:
        _print(result, args.json)
    if args.verbose and not args.json:
        for r in rows:
            if s.on and not (r["correct"] and r["reason_ok"] and r["customer_ok"]):
                unsafe = f"  {s('UNSAFE CLEARANCE', BOLD, RED)}" if r["unsafe"] else ""
                print(f"   {s(r['hit_id'], BOLD)}  {s('truth', GREY)} {s.decision(r['truth'])}  "
                      f"{s('pred', GREY)} {s.decision(r['pred'])}  {s('reason_ok', GREY)} {_value(s, r['reason_ok'])}"
                      f"  {s('customer_ok', GREY)} {_value(s, r['customer_ok'])}{unsafe}")
            elif not (r["correct"] and r["reason_ok"] and r["customer_ok"]):
                print(f"  {r['hit_id']}: truth={r['truth']} pred={r['pred']} "
                      f"reason_ok={r['reason_ok']} customer_ok={r['customer_ok']}"
                      f"{'  <-- UNSAFE CLEARANCE' if r['unsafe'] else ''}")
    return 1 if result["unsafe_clearances"] else 0


def cmd_repro(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = ["0", "1", "42", "1234", "987654"][: args.runs] + [
        str(i) for i in range(100, 100 + max(0, args.runs - 5))
    ]
    engine_args = ["--engine", args.engine] if args.engine else []
    s = _style(args)
    if s.on:
        print(_section(s, "REPRO", _width(), f"{s.num(len(seeds))} {s('fresh processes, different PYTHONHASHSEED', GREY)}"
                                              f"  {s('|', GREY)}  {s('engine', GREY)} {s(_mode(args), BOLD)}"))
    runs = []
    for i, seed in enumerate(seeds, 1):
        path = out_dir / f"run_{i}.jsonl"
        env = dict(os.environ, PYTHONHASHSEED=seed)
        t0 = time.perf_counter()
        with _live(s) as progress:
            progress.stage(f"run {i}/{len(seeds)}  seed {seed}")
            subprocess.run([sys.executable, str(SCREEN_PY), "run", args.data, str(path), "--quiet",
                            *engine_args], check=True, env=env)
        data = path.read_bytes()
        decisions = {}
        for line in data.decode("utf-8").splitlines():
            row = json.loads(line)
            decisions[row["hit_id"]] = (row["decision"], row["reason_code"])
        runs.append({"run": i, "pythonhashseed": seed, "sha256": hashlib.sha256(data).hexdigest(),
                     "seconds": round(time.perf_counter() - t0, 2), "decisions": decisions})
        if s.on:
            r, same = runs[-1], runs[-1]["sha256"] == runs[0]["sha256"]
            mark = s("✓ identical to run 1", BOLD, GREEN) if same else s("✗ DIFFERS from run 1", BOLD, RED)
            print(f"   {s(f'run {i}/{len(seeds)}', BOLD)}  {s('PYTHONHASHSEED', GREY)} {s(seed.ljust(7), CYAN)} "
                  f"{s('sha256', GREY)} {s(r['sha256'][:16], BOLD, CYAN)}{s(r['sha256'][16:24] + '…', GREY)}  "
                  f"{s.num(format(r['seconds'], '6.2f'))}"
                  f"{s('s', GREY)}  {mark if i > 1 else s('reference', GREY)}", flush=True)
    first = runs[0]["decisions"]
    unstable = sorted(h for h in first if any(r["decisions"].get(h) != first[h] for r in runs[1:]))
    report = {
        "engine": _mode(args),
        "runs": [{k: v for k, v in r.items() if k != "decisions"} for r in runs],
        "byte_identical": len({r["sha256"] for r in runs}) == 1,
        "hits": len(first),
        "unstable_hits": unstable,
        "decision_reproducibility": round(1 - len(unstable) / len(first), 4) if first else None,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if s.on:
        print("")
        ok = report["byte_identical"]
        print(f"   {s(' BYTE-IDENTICAL ' if ok else ' NOT IDENTICAL ', BOLD, BLACK if ok else WHITE, BG_GREEN if ok else BG_RED)}"
              f"  {s.num(len(runs))} {s('runs', GREY)}  {s('|', GREY)}  {s.num(report['hits'])} {s('hits', GREY)}"
              f"  {s('|', GREY)}  {s('decision reproducibility', GREY)} {s.num(report['decision_reproducibility'])}"
              f"  {s('|', GREY)}  {s('unstable hits', GREY)} {_value(s, report['unstable_hits'] or 0)}")
        print(f"   {s('report', GREY)} {s((out_dir / 'report.json').as_posix(), BOLD)}")
    else:
        _print(report, args.json)
    return 0 if report["byte_identical"] else 1


def cmd_summary(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    obj = {"data": str(ds.root), **ds.summary()}
    s = _style(args)
    if s.on:
        print(_section(s, "DATASET", _width(), s(Path(ds.root).as_posix(), BOLD)))
        _print_kv(s, {k: v for k, v in obj.items() if k != "data"})
    else:
        _print(obj, args.json)
    return 0


def _parse_pair(text: str) -> tuple[str, str]:
    parts = re.split(r"[:,/>|]+", text.strip().upper())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--pair wants CORE:CHECK, e.g. DIFFERENT_ENTITY:REVIEW or D:R")
    return tuple(DECISION_ABBR.get(p, p) for p in parts)  # type: ignore[return-value]


def engine_diff(ds) -> dict[str, Any]:
    """Core vs independent check on every hit (raw engine outputs, before the merge)."""
    from .core import core_results

    cores = core_results(ds)
    pairs: Counter[str] = Counter()
    status: Counter[str] = Counter()
    rows = []
    for hit_id in sorted(ds.hits):
        core = cores.get(hit_id)
        check, err = _safe_check(ds, hit_id)
        st = agreement(core, None if err else check)
        cdec = core_decision(core)
        c = cdec.value if cdec else "NONE"
        pairs[f"{c}:{check.decision.value}"] += 1
        status[st] += 1
        if st != "AGREE":
            rows.append({"hit_id": hit_id, "alert_id": ds.hits[hit_id].alert_id, "status": st,
                         "core": c, "check": check.decision.value,
                         "core_reason_code": (core or {}).get("reason_code"),
                         "check_reason_code": check.reason_code.value,
                         "core_customer": (core or {}).get("customer_record_id"),
                         "check_customer": check.customer_record_id})
    return {"hits": len(ds.hits), "status": dict(sorted(status.items())),
            "pairs": dict(sorted(pairs.items())), "rows": rows}


def _render_diff(s: Style, report: dict[str, Any], pair: str | None) -> None:
    w = _width()
    status = report["status"]
    print(_section(s, "CORE vs CHECK", w, f"{s.num(report['hits'])} {s('hits', GREY)}  {s('|', GREY)}  " +
                   "  ".join(f"{s.agreement(k)} {s.num(v)}" for k, v in status.items())))
    total = report["hits"] or 1
    for p, n in report["pairs"].items():
        c, k = p.split(":")
        tag = "" if c == k else s("  ← disagree", BOLD, YELLOW)
        print(f"   {s.num(f'{n:>5}')}  {s.decision(c).ljust(len(s.decision(c)) + 16 - len(c))} "
              f"{s(':', GREY)} {s.decision(k)}{' ' * max(1, 17 - len(k))}{_bar(s, n / total, 24, CYAN)}{tag}")
    print("")
    title = f"disagreements{' for ' + pair if pair else ''}"
    print(_section(s, title.upper(), w, s.num(len(report["rows"]))))
    for r in report["rows"]:
        print(f"   {s(r['hit_id'].ljust(10), BOLD)} {s.agreement(r['status'])}  {s('core', GREY)} "
              f"{s.decision(r['core'])}{s('/' + str(r['core_reason_code']), GREY)}  {s('check', GREY)} "
              f"{s.decision(r['check'])}{s('/' + str(r['check_reason_code']), GREY)}")
    if report["rows"]:
        print(f"   {s('Any disagreement goes to a second analyst (BORDERLINE); core-clears/check-confirms is never cleared.', GREY)}")


def cmd_diff(args: argparse.Namespace) -> int:
    s = _style(args)
    with _live(s) as progress:
        progress.stage("loading data")
        ds = load_dataset(args.data)
        progress.stage("both engines, every hit")
        report = engine_diff(ds)
    if args.pair:
        want = f"{args.pair[0]}:{args.pair[1]}"
        report["pair_filter"] = want
        report["rows"] = [r for r in report["rows"] if f"{r['core']}:{r['check']}" == want]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if s.on:
        _render_diff(s, report, report.get("pair_filter"))
        return 0
    print(f"{report['hits']} hits   " + "   ".join(f"{k}: {v}" for k, v in report["status"].items()))
    print("pairs (core:check):")
    for pair, n in report["pairs"].items():
        c, k = pair.split(":")
        print(f"  {n:>5}  {pair}{'' if c == k else '   <- disagree'}")
    print(f"disagreements{' for ' + report['pair_filter'] if args.pair else ''}: {len(report['rows'])}")
    for r in report["rows"]:
        print(f"  {r['hit_id']:<10} {r['status']:<8} core={r['core']}/{r['core_reason_code']}  "
              f"check={r['check']}/{r['check_reason_code']}")
    return 0


def queue_items(decisions: list[HitDecision], tier: str) -> list[dict[str, Any]]:
    items = []
    for d in decisions:
        if d.tier.value != tier:
            continue
        action = reviewer_action(d.reason)
        why = action or _first_sentence(d.reason)
        if ENGINES_DISAGREE in d.flags and not action:
            why = f"engines disagree; {why}"
        items.append({"hit_id": d.hit_id, "alert_id": d.alert_id, "decision": d.decision.value,
                      "reason_code": d.reason_code.value, "customer_record_id": d.customer_record_id,
                      "flags": list(d.flags), "why": why, "reviewer_action": action})
    return items


def cmd_queue(args: argparse.Namespace) -> int:
    mode = _mode(args)
    s = _style(args)
    with _live(s) as progress:
        progress.stage("loading data")
        ds = load_dataset(args.data)
        items = queue_items(_decide(ds, mode, progress), args.tier)
    shown = items[: args.limit] if args.limit else items
    report = {"engine": mode, "tier": args.tier, "total": len(items), "shown": len(shown), "items": shown}
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if s.on:
        w = _width()
        more = f"  {s(f'(showing {len(shown)})', GREY)}" if len(shown) < len(items) else ""
        print(_section(s, "QUEUE", w, f"{s.badge(args.tier)}  {s.num(len(items))} {s('hits', GREY)}{more}  "
                                      f"{s('|', GREY)}  {s(TIER_MEANING.get(args.tier, ''), ITALIC)}  {s('|', GREY)}  "
                                      f"{s('engine', GREY)} {s(mode, BOLD)}"))
        for it in shown:
            flags = f"  {s.flags(it['flags'])}" if it["flags"] else ""
            print(f"   {s(it['hit_id'].ljust(10), BOLD)} {s.verdict(it['decision'])}  {s(it['reason_code'], GREY)}"
                  f"  {s('customer', GREY)} {s.ids(str(it['customer_record_id'] or '-'))}{flags}")
            label = s("reviewer:", BOLD, YELLOW) + " " if it["reviewer_action"] else ""
            print("\n".join(_block(s, it["why"], w, "              " + label, "              ")))
        return 0
    print(f"{args.tier}: {len(items)} hits{f' (showing {len(shown)})' if len(shown) < len(items) else ''}"
          f"   [engine {mode}]")
    for it in shown:
        flags = f" [{', '.join(it['flags'])}]" if it["flags"] else ""
        print(f"  {it['hit_id']:<10} {it['decision']:<16} {it['reason_code']:<24}{flags}  {it['why']}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    wanted = [i.upper() for i in args.ids]
    unknown = [i for i in wanted if i not in BY_ID]
    if unknown:
        print(f"unknown policy id(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    rows = [p.to_dict() for p in REGISTRY if not wanted or p.id in wanted]
    if args.json:
        print(json.dumps({"policies": rows}, indent=2, ensure_ascii=False))
        return 0
    s = _style(args)
    if s.on:
        w = _width()
        print(_section(s, "POLICY REGISTRY", w, f"{s.num(len(rows))} {s('rules every decision is traced to', GREY)}"))
        kinds = {"sop": CYAN, "hofc": MAGENTA, "dual_control": YELLOW}
        for p in rows:
            kc = next((c for k, c in kinds.items() if k in str(p["kind"]).lower()), BLUE)
            print(f"   {s(p['id'].ljust(7), BOLD, kc)} {s(p['title'], BOLD)}  {s(p['kind'], GREY)}")
            print("\n".join(_block(s, p["effect"], w, f"           {s('effect', GREY)}  ", " " * 19)))
            print("\n".join(_block(s, "; ".join(p["implemented_by"]), w, f"           {s('code  ', GREY)}  ",
                                   " " * 19, GREY)))
            if p["triggers"]:
                print("\n".join(_block(s, "; ".join(p["triggers"]), w, f"           {s('when  ', GREY)}  ",
                                       " " * 19, GREY)))
        return 0
    for p in rows:
        print(f"{p['id']:<7} [{p['kind']}] {p['title']}")
        print(f"        effect: {p['effect']}")
        print(f"        implemented by: {'; '.join(p['implemented_by'])}")
        if p["triggers"]:
            print(f"        triggers: {'; '.join(p['triggers'])}")
    return 0


# --------------------------------------------------------------------------- stress


def _load_stress_module():
    """contrib/aml-bot/engine/stress_test.py (stdlib only). Only its perturbations are reused."""
    if not STRESS_PY.is_file():
        return None
    spec = importlib.util.spec_from_file_location("aml_bot_stress_test", STRESS_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # it imports the contrib engine (`screen`) for country codes
    return mod


def _decide_folder(folder: str | Path, mode: str) -> tuple[dict[str, str], str | None]:
    try:
        ds = load_dataset(folder)
        return {d.hit_id: d.decision.value for d in run(ds, mode)}, None
    except Exception as exc:  # noqa: BLE001 - a perturbation that breaks loading is reported, not fatal
        return {}, f"{type(exc).__name__}: {exc}"


def stress_report(data: str | Path, kinds: list[str] | None, mode: str) -> dict[str, Any]:
    st = _load_stress_module()
    if st is None:
        raise FileNotFoundError(f"stress test not found at {STRESS_PY}")
    src = str(data)
    kinds = kinds or list(st.KINDS)
    bad = [k for k in kinds if k not in st.KINDS]
    if bad:
        raise ValueError(f"unknown perturbation(s): {', '.join(bad)} (known: {', '.join(st.KINDS)})")
    base_tables = {t: st.read(src, t) for t in st.TABLES}
    variants: dict[str, set[str]] = {}
    nv_path = Path(src) / "name_variants.json"
    if nv_path.is_file():
        nv = json.loads(nv_path.read_text(encoding="utf-8"))
        for group in nv.get("given_name_groups", []):
            for n in group:
                variants[n.lower()] = set(group)
    base, err = _decide_folder(src, mode)
    if err:
        raise RuntimeError(f"baseline run failed: {err}")
    report: dict[str, Any] = {
        "engine": mode,
        "source": "contrib/aml-bot/engine/stress_test.py (perturbations) + screening.pipeline.run",
        "baseline": {"hits": len(base), "cleared": sum(v == "DIFFERENT_ENTITY" for v in base.values())},
        "kinds": {},
    }
    total = 0
    for kind in kinds:
        rng = st.random.Random(7)
        tmp = tempfile.mkdtemp(prefix=f"stress_{kind}_")
        try:
            tables = json.loads(json.dumps(base_tables))
            junk = st.perturb(tables, kind, rng, variants)
            for t, rows in tables.items():
                st.write(tmp, t, rows, junk=junk)
            for extra in sorted(os.listdir(src)):
                if extra not in st.TABLES and os.path.isfile(os.path.join(src, extra)):
                    shutil.copy(os.path.join(src, extra), tmp)
            new, error = _decide_folder(tmp, mode)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        flips: Counter[str] = Counter()
        unsafe = []
        for h in sorted(base):
            nd = new.get(h, "MISSING")
            if nd != base[h]:
                flips[f"{base[h]}->{nd}"] += 1
                if nd == "DIFFERENT_ENTITY":
                    unsafe.append(h)
        total += len(unsafe)
        report["kinds"][kind] = {"flips": dict(sorted(flips.items())), "unsafe": unsafe, "error": error}
    report["total_unsafe"] = total
    return report


def cmd_stress(args: argparse.Namespace) -> int:
    try:
        report = stress_report(args.data, args.kinds, _mode(args))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        b = report["baseline"]
        print(f"baseline: {b['hits']} hits, {b['cleared']} cleared   [engine {report['engine']}]")
        for kind, r in report["kinds"].items():
            extra = f"  e.g. {r['unsafe'][:5]}" if r["unsafe"] else ""
            err = f"  ERROR {r['error']}" if r["error"] else ""
            print(f"{kind:20s} unsafe={len(r['unsafe']):3d}  flips={r['flips']}{extra}{err}")
        print(f"TOTAL UNSAFE FLIPS: {report['total_unsafe']}")
    return 1 if report["total_unsafe"] else 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="screen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, engine: bool = False) -> None:
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if engine:
            sp.add_argument("--engine", choices=ENGINES, default=None,
                            help="core | check | combined (default: $SCREEN_ENGINE or combined)")

    sp = sub.add_parser("run", help="decide every hit")
    sp.add_argument("data")
    sp.add_argument("out")
    sp.add_argument("--trace", help="also write tiers, flags and rule trace (dashboard input)")
    sp.add_argument("--quiet", action="store_true")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("explain", help="evidence trail, core vs check, policies for one hit")
    sp.add_argument("hit_id")
    sp.add_argument("--data", default="data/dev")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("eval", help="score against practice labels")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--pred", help="score an existing output file instead of running")
    sp.add_argument("--compare", action="store_true", help="score core, check and combined side by side")
    sp.add_argument("-v", "--verbose", action="store_true")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("repro", help="prove N runs are identical")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--runs", type=int, default=5)
    sp.add_argument("--out-dir", default="output/repro")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_repro)

    sp = sub.add_parser("summary", help="dataset counts")
    sp.add_argument("--data", default="data/dev")
    common(sp)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("diff", help="decision core vs independent check disagreements")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--pair", type=_parse_pair, help="CORE:CHECK filter, e.g. DIFFERENT_ENTITY:REVIEW or D:R")
    common(sp)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("queue", help="analyst queue for one tier")
    sp.add_argument("--tier", required=True, type=str.upper, choices=TIERS)
    sp.add_argument("--limit", type=int, default=0, help="show at most N hits (0 = all)")
    sp.add_argument("--data", default="data/dev")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_queue)

    sp = sub.add_parser("policy", help="policy registry (SOP, Head of FinCrime calls, dual control)")
    sp.add_argument("ids", nargs="*", help="only these ids, e.g. HOFC-3 DC-1")
    common(sp)
    sp.set_defaults(func=cmd_policy)

    sp = sub.add_parser("stress", help="perturbation stress test (contrib/aml-bot) against our engine")
    sp.add_argument("--data", default="data/dev")
    sp.add_argument("--kinds", nargs="*", help="perturbations to run (default: all 21)")
    common(sp, engine=True)
    sp.set_defaults(func=cmd_stress)
    return p


def _utf8_stdio() -> None:
    """Names carry accents (Jovanovic with a caron, etc.); Windows pipes default to cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)
