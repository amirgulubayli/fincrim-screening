# Human-in-the-loop design

Audience: compliance reviewers. The engine never replaces the analyst; it decides only what the SOP lets it decide on clear evidence, and tells a person exactly why.

## Tiers

| Tier | Decision | Rule | Human action |
|---|---|---|---|
| `AUTO_CLEAR` | DIFFERENT_ENTITY | a solid fact contradicts the hit (SOP 6) | none; sampled QA |
| `AUTO_ESCALATE` | SAME_ENTITY | name + DOB (person) or name + country (company) agree, nothing contradicts (SOP 7) | compliance team handles the true match |
| `BORDERLINE` | DIFFERENT or SAME | the deciding fact is weak (flags below) | second pair of eyes confirms or overrides |
| `HUMAN_REVIEW` | REVIEW | nothing settles it, or several customers disagree (SOP 3, 7) | analyst decides |

Logic lives in `system/screening/triage.py`; the scored output keeps the decision, the tier is extra.

## Weak-evidence flags (route to BORDERLINE)

A rule sets `weak=True` with a `weak_why` flag when the fact it used is shaky. Any weak fact in the deciding set makes the tier BORDERLINE.

| Flag | Meaning | SOP |
|---|---|---|
| `DOB_FROM_SEARCH_RESULT` | the hit's birth date came only from a web search result judged to be about the hit | 5 |
| `DOB_FROM_AGE_AT_DATE` | birth year derived from "aged N in <date>" (two possible years) | 5 |
| `NAME_EDIT_DISTANCE_ONLY` | names agree only through edit-distance tolerance, no exact or variant match | 8 |
| `FACT_FROM_NOTE` | customer fact (DOB, country, document) taken from a support note, not an approved KYC check | 4 |
| `NO_CUSTOMER_RECORD` | no customer record resolved; decided on screened details only (added by triage) | 2, 9 |
| `SINGLE_LOCATION_CONTRADICTION` | clearance rests on one country comparison with few countries on either side | 6 |
| `DECLARED_NOT_VERIFIED` | name or DOB from the customer record because no approved check exists | 4 |
| `PLACEHOLDER_NAME` | customer name has placeholder parts | 8 |

Rule authors add flags here when they introduce one.

## Analyst review in the dashboard

1. Queue sorted by tier: HUMAN_REVIEW, then BORDERLINE, then a QA sample of AUTO tiers.
2. Each hit shows customer side (resolved records, approved KYC, notes) next to hit side (names, DOBs, countries, dated reports).
3. The rule trace lists every check: rule id, signal, one-line summary, evidence IDs, weak flags.
4. Analyst accepts or overrides with a reason; the override is stored next to the engine decision.
5. An optional LLM narrative (Grok via OpenRouter) can rephrase the trace for reading. It is labelled as such and never changes the decision.

## Audit trail

Each decision records:
- `reason_code` and a 2-4 sentence template reason built from rule summaries.
- `evidence`: only the IDs that decided it (customer record, KYC check, NOTE, hit, SR) - SOP 9.
- `trace`: every rule result with `rule_id` (e.g. `P6.dob`), so a reviewer can map each step to the SOP clause.
- `flags` and `tier`; engine version from `screen --version`.

Same input always gives the same bytes (`screen repro`), so any decision can be reproduced later.

## Why the LLM is not in the decision path

- Reproducibility: the same hit must get the same decision and reason every run.
- Auditability: every decision maps to a named SOP clause and record IDs, not a model's judgement.
- Safety: an unsafe clearance costs 30x a needless review; deterministic rules can be tested to never clear without a contradiction.
- The brief requires the scored system to be stdlib-only and offline.

## Mapping to SOP principles (paraphrased)

| # | Principle | Where |
|---|---|---|
| 1 | Decide identity only, not risk | scope of all rules |
| 2 | Resolve the customer first, through links or screened details | `resolve.py` |
| 3 | Merge records of one person; several persons -> AMBIGUOUS | `resolve.py`, `compiler.combine_persons` |
| 4 | Latest approved identity check outranks declared data | `resolve.py`, `FACT_FROM_NOTE` / `DECLARED_NOT_VERIFIED` flags |
| 5 | Hit facts from fields, profile notes, relevant search results | `facts.py`, `DOB_FROM_*` flags |
| 6 | Clear only on a contradiction (time, DOB, location, name, type) | `rules/*`, compiler step 1 |
| 7 | Confirm only on agreement; else REVIEW | compiler steps 2-3 |
| 8 | Name comparison rules | `normalize/names.py`, `rules/name.py` |
| 9 | Cite the deciding records | `explain.evidence_ids` |
