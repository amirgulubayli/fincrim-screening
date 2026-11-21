# AML Bot findings and reference engine (Track 2)

Independent reference implementation and review findings from AML Bot, built with Ozwald (Head of Financial Crime). It does **not** touch the team engine in `system/`; it lives here so its rules, tests and stress-test findings can be compared and ported.

## Contents
- `engine/` - standalone stdlib-only rules engine (`engine/run <data folder> <output file>`), unit + robustness tests, stress test, README with architecture and worked cases.
- `output/dev.jsonl` - its decisions on all 819 dev hits (dev data rebuilt from the workbook, not the official JSONL).
- `docs/` - framework, data profile and the review packs from each round (round 1 -> hardening).

## Headline results (dev, practice labels)
- 49/50 practice labels correct (decision score 97.2%), **0 unsafe clearances**; 5 runs byte-identical; ~10-15 s per run; no LLM.
- 819 hits: 430 DIFFERENT_ENTITY / 123 SAME_ENTITY / 266 REVIEW; 155 of 440 alerts fully cleared.
- Realistic expectation on unseen data: ~60-85% (rules were tuned on visible labels).

## Policy calls made by the Head of Financial Crime (worth adopting in `system/`)
1. Only one shared name part with the verified legal name: clear only when given names are full and clearly differ; otherwise REVIEW with "Escalated, not cleared: ... Reviewer: ..." guidance.
2. Shared surname that is commonly a father's given name (Umar, Ahmed, Abubakar, Ibrahim, Suleiman...): REVIEW, order unreliable.
3. Countries that were one state (former Yugoslavia, USSR, Czechoslovakia, Sudan/South Sudan, Ethiopia/Eritrea): REVIEW, not a location clearance.
4. Hit whose only country is a business address (individuals): REVIEW.
5. Several customers fit and all lead to clearance: clear with customer AMBIGUOUS.
6. Ages inside news-report paragraphs are ignored (safe direction).
7. All countries in customer notes count for the location rule.
8. H-2808BE (possible name reorder) deliberately left in REVIEW rather than tuned to 50/50.

## Stress-test findings (see `docs/review_hardening.md`)
21 perturbations of the data found 25 unsafe flips from 3 causes, all fixed: an unreadable DOB hiding a name+DOB match; a partial DOB blocking a same-person merge; misspelt given-name variants. Missing or garbled data now only moves hits towards REVIEW. An independent verifier re-derives every clearance.
