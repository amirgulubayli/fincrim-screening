# Human-in-the-loop design

Audience: compliance reviewers. The system decides identity only ("is this hit our customer?"), never risk appetite.
It automates what the SOP lets it decide on clear evidence, asks for a second pair of eyes when the evidence or the two
engines are not fully aligned, and hands everything else to an analyst with the reason and the records.

## What is automated, what is borderline, what needs a person

| Tier | Decision | When | Human action | Dev count (of 819) |
|---|---|---|---|---|
| `AUTO_CLEAR` | DIFFERENT_ENTITY | a cited contradiction, reproduced by the core's `second_check`, and the independent check agrees | none; periodic QA sample | 397 |
| `AUTO_ESCALATE` | SAME_ENTITY | name + DOB (person) or name + country (company) agree, nothing contradicts, both engines agree | compliance handles the true match (EDD / PEP / asset-freeze process) | 98 |
| `BORDERLINE` | DIFFERENT or SAME | the engines disagree, or the deciding fact is weak | a second, different analyst confirms or overrides (4-eyes) | 58 |
| `HUMAN_REVIEW` | REVIEW | nothing settles it, a policy call escalates it, several customers disagree, or the engines conflict | an analyst decides | 266 |

The scored output carries the decision; the tier and flags are extra fields in the trace and the dashboard.
BORDERLINE on dev: 54 rest on a DOB taken only from a web search result, 5 on a fuzzy name match, 2 on engine
disagreement (some carry several flags).

## Dual control: two engines, fixed merge rules

The **decision core** (AML Bot engine by akhackathon222, with the Head of Financial Crime's policy calls and its own
`second_check`) and the **independent check** (the team's modular rules) are separate code bases that decide every hit.

| Situation | Result | Why |
|---|---|---|
| Both agree | that decision; AUTO tier unless a weak fact | two independent derivations |
| Core clears, check **confirms** the match (name + DOB agree) | **REVIEW** (`ENGINES_CONFLICT`) | never clear on a conflict; clearing a true match is the costliest error |
| Any other disagreement (usually: core decides, check says REVIEW) | core decision stands, tier **BORDERLINE** (`ENGINES_DISAGREE`) | the decision is plausible but not doubly supported: 4-eyes |
| Core REVIEW, check would decide | REVIEW | the cautious opinion wins |
| Core gives no answer, check clears | **REVIEW** (`CORE_MISSING`) | the check alone never clears |
| Any exception for a hit | REVIEW (`ENGINE_ERROR:*`, `CHECK_ERROR:*`) | errors never clear |

Inside the core, `second_check` re-derives every clearance from the raw tables; if it cannot reproduce the
contradiction, or unmerged records share the customer's phone with a partial DOB, the hit becomes REVIEW before the
merge sees it. So an automated clearance has passed three gates: the core's rules, its `second_check`, and the
independent check not confirming the match.

## Flags a reviewer sees

| Flag | Meaning | Effect |
|---|---|---|
| `ENGINES_DISAGREE` | the two engines reached different decisions | BORDERLINE (or stays HUMAN_REVIEW if REVIEW) |
| `ENGINES_CONFLICT` | core cleared, check confirmed the match | REVIEW |
| `ESCALATED_BY_POLICY` | a Head-of-FinCrime policy call or `second_check` held the hit back; reason starts "Escalated, not cleared" and ends "Reviewer: ..." | REVIEW |
| `DOB_FROM_SEARCH_RESULT` | the hit's DOB came only from a web search result judged to be about the hit | BORDERLINE |
| `NAME_FUZZY_MATCH` | names agree only through edit-distance tolerance | BORDERLINE |
| `NAME_FROM_SCREENED_ONLY`, `COUNTRY_FROM_SCREENED_ONLY` | the customer fact came from the screened details, not a record | BORDERLINE |
| `NO_CUSTOMER_RECORD` | no customer record resolved | BORDERLINE |
| `CUSTOMER_FOUND_BY_SEARCH` | the alert had no link; the customer was found by name / DOB / country / city | information only |
| `CORE_MISSING`, `ENGINE_ERROR:*`, `CHECK_ERROR:*` | an engine could not evaluate the hit | REVIEW / information |

## Policy calls by the Head of Financial Crime (always the cautious reading)

1. Name + partial DOB confirms (SAME_ENTITY stands).
2. A location difference never clears a name + DOB match: REVIEW.
3. Only one name part shared with the verified legal name: clear only when it is the surname on both sides and both
   given names are full and clearly different; otherwise REVIEW.
4. Surnames that are commonly a father's given name (e.g. Umar, Ahmed, Ibrahim): never cleared on name mismatch.
5. Countries that were once one state (former Yugoslavia, USSR, Czechoslovakia, Sudan/South Sudan, Ethiopia/Eritrea): REVIEW, not a location clearance.
6. A hit whose only country link is a business address (individuals): REVIEW.
7. Several customers fit and all lead to clearance: clear, customer `AMBIGUOUS`.
8. Ages inside news-report paragraphs are ignored (more reviews, no clearances).
9. Every country in the customer's notes counts towards their country set (fewer location clearances).

Source and examples: `contrib/aml-bot/engine/README.md`.

## Reviewer actions (analyst dashboard, `app/`)

1. **Queue** in the order work is needed: HUMAN_REVIEW, then BORDERLINE, then AUTO_ESCALATE, then a QA sample of AUTO_CLEAR.
   Each row has a one-line "why this tier"; filter by reason code, flag and case status.
2. **Case file**: customer side (resolved records, approved KYC, notes) next to hit side (names, DOBs, countries,
   dated reports); agree / contradict / not decisive markers from the trace; evidence chips open the underlying record.
3. **Trace**: the core's verdict (`CORE.aml`) and every check result (`P6.type`, `P6.dob`, ...) with summary, evidence IDs and weak flags.
4. **Act**: accept, or override to SAME_ENTITY / DIFFERENT_ENTITY / REVIEW. A name and a note are always required.
5. **4-eyes**: BORDERLINE cases, and any analyst clearance of a hit the engine did not clear, need a second, different analyst.
6. **Audit trail**: every action appended (who, when, what, note, case status) and exportable as CSV. The engine output is never edited.
7. **Explain**: an analyst can ask Grok (read-only plugin) "why was H-xxx cleared?". It answers only from the trace
   and has no tool that can change a decision, write data or edit code.

## Audit trail per decision

- `reason_code` and a 2-4 sentence reason naming the decisive facts and record IDs.
- `evidence`: only the IDs that decided it (customer record, KYC check, note, search result) - SOP 9.
- trace: the core's verdict plus every check rule result, each `rule_id` mapped to its SOP clause.
- `tier`, `flags`, engine version. Same input always gives the same bytes (`screen repro`), so any decision can be
  reproduced later for audit or QA.

## UK regulatory framing

- **MLRs 2017.** Supports customer due diligence and ongoing monitoring (regs 27-28) and enhanced due diligence / PEP
  obligations (regs 33-35). A SAME_ENTITY on a PEP hit feeds the EDD/PEP process; the identity decision is kept
  separate from risk appetite.
- **OFSI (SAMLA 2018 regimes).** A sanctions SAME_ENTITY goes straight to compliance for asset-freeze and reporting
  decisions. The design minimises the costliest error, clearing a true match: every clearance needs a cited
  contradiction that holds against all verified identity data and survives two independent checks.
- **FCA SYSC 6.3 and the Financial Crime Guide (FCG 3, FCG 7).** Documented, consistent alert handling; a recorded
  rationale and evidence per hit; reproducible results for audit and QA; escalation with reviewer guidance; a
  policy-change log owned by the Head of Financial Crime. Suggested controls:
  - 4-eyes on BORDERLINE and on any analyst clearance of a non-cleared hit (built into the dashboard);
  - periodic QA sampling of AUTO_CLEAR by reason code, especially `NAME_MISMATCH_VERIFIED` and `LOCATION_MISMATCH`;
  - re-validation (`screen eval`, `screen repro`) whenever the SOP, the policy calls or the screening lists change.

## Why no LLM in the decision path

- **Reproducibility**: the same hit must get the same decision and reason every run.
- **Auditability**: every decision maps to a named SOP clause and record IDs, not a model's judgement.
- **Safety**: an unsafe clearance costs about 30x a needless review; deterministic rules can be tested never to clear
  without a contradiction (`screen eval` fails the build on any unsafe clearance).
- **The brief**: the scored system is standard library only and offline.

## Mapping to SOP principles (paraphrased)

| # | Principle | Core | Check |
|---|---|---|---|
| 1 | Decide identity only, not risk | scope | scope |
| 2 | Resolve the customer first, through links or screened details | `resolve_customer` | `resolve.py` |
| 3 | Merge records of one person; several persons -> AMBIGUOUS | person merge | `identity.py`, `compiler.combine_persons` |
| 4 | Latest approved identity check outranks declared data | verified identity | `resolve.py` |
| 5 | Hit facts from fields, profile notes, relevant search results | hit facts | `facts.py`, `DOB_FROM_SEARCH_RESULT` |
| 6 | Clear only on a contradiction (time, DOB, location, name, type) | rules + `second_check` | `rules/*`, compiler step 1 |
| 7 | Confirm only on agreement; else REVIEW | confirm rule | compiler steps 2-3 |
| 8 | Name comparison rules | name normaliser + middle rule | `normalize/names.py`, `rules/name.py` |
| 9 | Cite the deciding records | evidence lists | `explain.evidence_ids` |
