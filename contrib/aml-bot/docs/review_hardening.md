# Hardening round: review pack (Track 2)

None of Ozwald's policy calls changed. H-2808BE stays REVIEW. No GitHub push.

## Result on dev
- **Practice labels:** 49/50, score 97.2%, **0 unsafe clearances**. Same as round 3.
- **5 runs byte-identical:** MD5 952ca6cbabaf5bc75c0690a036fe542a. Run time about 10–15 s.
- **Decisions changed on dev: 0.** The decision distribution is unchanged: DE 430 / SAME 123 / REVIEW 266.
- **Two reason codes changed (still DE):** H-957A5D and H-FA5871 moved from LOCATION_MISMATCH to NAME_MISMATCH_VERIFIED.
  - Cause: a bug in the old country parser, which turned KYC note fragments such as "NIGERIA VERIFIED VIA VERIFF" into fake country tokens.
  - Those fragments are now recognised properly or flagged unknown. The clearance now rests on the verified-name mismatch, and the second check confirms it.

## 1. Perturbation stress test (`system/stress_test.py`)
21 perturbations:
- dates: long, US, yyyy/mm/dd, dotted, 2-digit years, month-first slashes, year-only;
- name order;
- diacritics added / stripped;
- transliteration variants;
- countries as ISO-2, ISO-3 and upper case;
- nulled non-identity fields;
- case and whitespace noise;
- extra fields;
- renamed fields;
- lists instead of strings;
- junk lines plus BOM;
- combined.

**First run: 25 unsafe flips** (REVIEW/SAME → DE) from three root causes, all fixed:

| Cause | Example | Fix |
|---|---|---|
| An unreadable DOB made a name+DOB match "disappear", so a location clearance fired | H-ADF453 under 2-digit years | A present-but-unreadable DOB blocks DOB and temporal clearances, and blocks location when the names agree |
| An imprecise DOB stopped the phone+full-DOB merge, so the second record and its country were lost | H-5AF3A7 (the Pakistan record dropped) | Records that share a phone and are unmerged only because a DOB is partial or unreadable now block any clearance; an unreadable record DOB no longer rules a candidate out during unlinked search |
| A misspelt variant ("Christohper") did not link to its variant group ("Cristobal"), so the surname middle rule cleared | H-4C3C21 | A wider `variant_link` (fuzzy match onto any group member) used only to block the middle-rule clearance |

**Final run: 0 unsafe flips across all 21.** Losing information now only moves hits towards REVIEW. For example, 2-digit years everywhere turn 264 DE into REVIEW and create no new clearances.

## 2. Fail-safe parsing
- **Dates:**
  - Strict calendar validation.
  - 2-digit years, free text and impossible dates are unknown.
  - An ambiguous dd/mm vs mm/dd date keeps only the year when the convention is not certain. Hit side: always, because listings mix both conventions (23 month-first vs 4 day-first slash dates in dev hits). Customer side: dd/mm only when no month-first date exists in the customer data, which holds in dev.
- **Countries:**
  - A shipped ISO table (`countries.json`, 250 countries plus aliases and demonyms; plain data, the runtime stays stdlib-only).
  - An unrecognised value becomes "unknown" and blocks location clearance.
  - Addresses are scanned for exactly one country name.
- **Ages:** unchanged from round 3 (name/he/she anchoring, unit rejection, news paragraphs skipped).

## 3. Second check (`second_check`)
- An independent verifier re-derives every DE from the raw tables. If it can't reproduce the contradiction, the hit is downgraded to REVIEW with the reason "independent second check could not reproduce…".
- On dev it reproduces 430/430 clearances.
- A test with a deliberately wrong first pass shows the downgrade working.

## 4. Crash-proofing and tolerant loader
- **Per alert and per hit:** an error guard turns any failure into REVIEW / INSUFFICIENT_DATA with a reason.
- **Inputs handled:**
  - bad JSON lines and non-object lines are skipped with a warning;
  - BOM and encoding errors;
  - missing or empty files and a missing name_variants.json;
  - orphan hits still get a line.
- **Field names:** normalised, with an alias map of schema variants. An alias never overwrites a real field. Lists, objects and "; "-strings are all accepted.

## Tests
**36 tests, all passing:** 19 rule tests plus 17 robustness tests covering fail-safe dates and countries, the loader, crashes, the second check, possible links and perturbation invariants.

## Holdout honesty: realistic expected range on unseen data
The 97% is optimistic, for three reasons:
- **The rules were tuned on these 50 labels.** Round 1 scored 16.7% on them. Rounds 2–3 fixed rules after looking at label misses (H-4D76E3, the age-extraction bugs, the middle rule).
- **The labels cover only part of what the system does:** 50 of 819 hits (6%). They contain no DECEASED, ENTITY_TYPE, AMBIGUOUS_CUSTOMER_RECORD or NAME_DOB_MATCH outcomes, and only 2 NAME_MISMATCH_VERIFIED and 1 TEMPORAL case.
- **The free-text extraction is regex-based,** tuned on dev wording.

Rough cost model for about 550 hits with dev's mix (about 55% DE truth, so the all-REVIEW baseline costs about 900):
- Each DE left in REVIEW costs 3, about 0.3 pts.
- Each unsafe clearance costs 60, about 6–7 pts.
- If 10–25% of true DEs end up in REVIEW, that costs 10–25 pts.
- If 0–3 unsafe clearances slip through, that costs 0–20 pts.
- SAME/REVIEW mix-ups cost about 3–5 pts.

**Realistic decision score on unseen: about 60–85%, central estimate about 75%.** The most important lever is keeping unsafe clearances at zero, because one of them costs as much as twenty missed clearances. That is what this round targeted.

## Remaining risks
1. **New free-text phrasing** (ages, residence notes, KYC notes) on unseen data. These fail safe to REVIEW, which costs points.
2. **Field names outside the alias map.** A completely different schema would load as empty fields and send everything to REVIEW, which is safe but scores about 0. Check against the official data room / `validate.py` before submitting.
3. **Clearances on genuinely different-looking data.** Clearances still rely on the policy as encoded (e.g. a location mismatch when no DOB is held). The stress test checks formatting changes, not new kinds of facts.
4. **The second check reuses some shared parsers** (dates, countries, names, `hit_profile` DOBs). It guards against rule-logic errors more than parser errors.
5. **Time spent:** this round ran over the ~40 min timebox, mainly because each stress run takes about 8 minutes.

## Files
- **Code:** `system/screen.py`, `system/stress_test.py`, `system/countries.json`, `system/tests/test_robustness.py`, and `system/README.md` (new Robustness section).
- **Output:** `output/dev.jsonl`.
- **Stress log:** `/workspace/aml/stress_final.txt`.
