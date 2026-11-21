# Stress findings: combined system on perturbed dev data (issue #34)

Tool: `uv run python tools/stress_system.py <data> [--kinds ...] [--modes combined check] [--workers 4] [--markdown]`.
It applies the 21 perturbations of `contrib/aml-bot/engine/stress_test.py` (copied) to a temp copy of the data
outside the repo, runs `system/screen.py run` in fresh processes (`SCREEN_ENGINE=combined` and `check`), and
counts **unsafe flips** (SAME/REVIEW on clean data -> DIFFERENT_ENTITY on the perturbed copy) and crashes.
Exit 1 on any unsafe flip or crash in `combined` (the scored mode).

## Result

Before = first run on `main` (1436fa7). After = this branch (loader + pipeline fixes).

| perturbation | combined before | combined after | check before | check after |
|---|---|---|---|---|
| dates_long, dates_us, dates_slash_unamb, dates_dotted | 0 | 0 | 0 | 0 |
| dates_two_digit | 0 (264 DE->REVIEW) | 0 | 10 | 1 (H-AF6CD2) |
| dates_us_slash | 0 | 0 | 1 (H-5AF3A7) | 1 (H-5AF3A7) |
| dates_year_only | 0 | 0 | 7 | 0 |
| name_order, diacritics_add/strip, transliterate | 0 | 0 | 0 | 0 |
| country_alpha2 / alpha3 / upper | 0 | 0 | 0 | 0 |
| null_non_identity, case_whitespace, extra_fields, list_vs_string | 0 | 0 | 0 | 0 |
| renamed_fields | 0 | 0 | 27 | 0 |
| junk_lines | **CRASH** (no output at all) | 0 | CRASH | 0 |
| combined | **CRASH** (no output at all) | 0 | CRASH | 0 |

Combined mode: **0 unsafe flips** before and after; the only combined failure was the crash. Combined-mode
"after" was re-run only for `junk_lines` and `combined` (the pipeline guard can only turn a check clear into
REVIEW, and in combined mode the core decides, so it cannot create a combined clearance; the loader change only
affects files with a BOM / bad lines). Dev eval unchanged: 0.9722, 0 unsafe.

## F1 (fixed, loaders.py) - a BOM or one bad line crashed the whole run

`loaders.read_jsonl` raised `ValueError` on the first bad JSON line, and opened files as plain `utf-8`, so a
file starting with a UTF-8 BOM failed on line 1. `cmd_run` then died before writing anything: **every hit
unanswered** on the scored run. The core already tolerates both (its own loader skips bad lines with a warning).
Fix: read with `utf-8-sig`, skip bad-JSON and non-object lines into `loaders.LOAD_WARNINGS`. Skipping only
removes facts; missing facts never clear, and the core reads the folder independently.

## F2 (fixed in pipeline.py, root cause in rules/location.py) - check clears on a country difference when a date is present but unusable

All 45 check-mode unsafe flips were `LOCATION_MISMATCH` (one `NAME_MISMATCH_VERIFIED`, H-AF6CD2, under
2-digit years) on hits whose names agree. `rules/location.py` blocks a country clear when the customer and hit
share a birth year (`_shared_birth_years`), but that safety net needs readable dates. It goes silent when:

* a DOB is in a format the parser rejects (2-digit years `04/03/75`, `circa 1971`, month-first slashes kept
  only as a year) - e.g. H-ADF453, H-D05706 (SAME/NAME_DOB_MATCH on clean data);
* the DOB sits under a field name the loader does not map (`dates_of_birth`, `Date of Birth`,
  `date_of_birth`) and lands in `extra` - 27 flips under `renamed_fields`;
* the screening date is year-only, so ages in search results / profile notes give no birth year.

The core does not clear these (it has "a present-but-unreadable DOB blocks location when the names agree"),
so combined mode stayed safe, but the dual control was blind on exactly these hits.

Fix (no rules/ edit, other agents own it): `pipeline._guard_location_clear` turns a check verdict of
DIFFERENT_ENTITY / LOCATION_MISMATCH into REVIEW / INSUFFICIENT_DATA when a NAME agreement is in the trace and
`_date_unusable` finds an unreadable hit/customer/KYC/alert DOB, a DOB-like key in `extra`, or an imprecise
`screened_at`. Suggested follow-up for the rules owner: move this into `rules/location.py` (abstain instead
of contradicting) and add a field-alias map to `contracts.from_raw` like the core's `FIELD_ALIASES`.
Check mode after the fix: 45 -> 2 unsafe flips, both check-only (the core keeps them REVIEW, so combined
is unaffected), left for their owners:

* H-AF6CD2 under 2-digit years -> `NAME_MISMATCH_VERIFIED` (was REVIEW/AMBIGUOUS_CUSTOMER_RECORD). Not traced; likely with the
  DOBs unreadable the check's resolver keeps a different candidate set and the name rule clears; owner:
  resolve.py / rules/name.py.
* H-5AF3A7 under month-first slash dates -> `LOCATION_MISMATCH`. Likely (not traced here) the root cause the core fixed in its
  hardening round: records that share a phone are merged only on a full DOB match, so an imprecise DOB drops
  the second record (and its Pakistan link). Proposed fix in `resolve.py`: if two records share a phone and
  the DOB comparison is only "not contradicting" (partial/unreadable), keep them as a possible link that
  blocks any clearance. Owner: resolve.py.

## F3 (core, NOT edited) - unreadable alert DOBs make the core's unlinked search quadratic

`aml_engine.resolve_customer`, search branch (no account/login/email on the alert):

```python
pool = D.by_year.get(sd[0], []) + D.by_year.get(None, []) if sd else D.customers.keys()
cands = [r for r in pool if fits_screened(D, r, alert)]
```

With an unreadable alert DOB (and unreadable customer DOBs, which all go to `by_year[None]`) every one of the
146 search alerts scans all 72,265 customers through `Names.agree`, which re-tokenises the customer name
each time. Measured: ~1.1 s per alert on an idle core. `dates_two_digit` took **1,546 s** for one combined run
versus ~95 s normally. Decisions stay safe (264 DE -> REVIEW, 0 unsafe), but a similar unseen format would
risk a run-time limit. It is not a safety flip, so nothing is changed here.

Proposed patch for the core owner (pure memoisation, no decision change; verify with `repro --runs 5`):

```python
class Names:
    def __init__(self, ...):
        ...
        self._parts_cache = {}

    def parts(self, name, company=False):
        key = (name, company)
        got = self._parts_cache.get(key)
        if got is None:
            got = self._parts_cache[key] = self._parts_uncached(name, company)  # current body
        return got
```

If that is not enough: precompute `parts()` for every customer name once in `Data.__init__` and give
`fits_screened` a cheap superset prefilter (lengths within 2 of some alert part, or a shared variant group)
before `agree`. The prefilter must never drop a record `agree` would accept (dropping a candidate in search
can change who the customer is).

## Unseen-style fixtures

`tests/fixtures/unseen/` (built by `tests/fixtures/make_unseen.py`, all invented) +
`tests/test_unseen_fixtures.py`: compact `19750304` and Roman-numeral `04.III.1975` DOBs, a DOB only in a KYC
note with new wording, `circa 1971` on the hit, 2-digit-year `04-03-75` with `Türkiye` vs `Turkey`, an
unrecognised country spelling (`Rep. of Korea`), and a BOM + junk line in notes.jsonl. Asserts no clearance in
combined, core and check modes. Before the F2 guard the check cleared H-U3 (`circa 1971`, UK vs Nigeria).
