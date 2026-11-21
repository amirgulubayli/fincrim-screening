# Track 2 review framework: "Is my customer sanctioned?"
Scope: identity only (SAME_ENTITY / DIFFERENT_ENTITY / REVIEW per hit). It does not cover risk appetite, SAR or exit decisions; those come after a SAME_ENTITY result.

## Scoring economics (from the full brief)
Cost: clearing a real or uncertain match = 60; a wrong REVIEW on a different entity = 3; a wrong SAME on a different entity = 3; SAME vs REVIEW confusion = 2.
- Only clear when you are at least about 95% sure (3/63). Clear **on a hard, cited contradiction only**.
- SAME vs REVIEW is low-stakes and symmetric, so confirm whenever the policy's agreement test is met.
- The score is the cost saved versus "review everything". The money is in safely clearing false positives, which make up most hits (28/50 practice labels).
- Weighting: 80% unseen set (about 550 hits, 5 runs), 10% dev, 10% reproducibility. Within each, decisions are 75% and reasons are 25% (code, minimal evidence IDs, customer record, faithful reason).

## Pipeline (deterministic core, LLM only for extraction)
1. **Normalise:** dates in 5+ formats, keeping partial dates partial. Countries: ISO-2, names and demonyms mapped to one code; drop "International"/"Europe". Names: casefold, strip accents, punctuation and brackets, remove placeholders and legal forms, apply name_variants groups.
2. **Resolve the customer** (policy P2–P4):
   - Follow the link (account_ref / login_handle / email). For a joint account, the screened name and DOB pick the holder.
   - With no link (146 alerts), use blocked candidate search: name agrees (P8) AND the DOB is equal in every part both give AND the country is residence or nationality AND the city matches if screened.
   - Expand to "one person": same phone + same full DOB, or the same approved document number, applied transitively.
   - Pull all facts: records, the latest *approved* KYC check (via Onboarding→applicant) or KYC-channel note, and residence notes. Rejected or expired checks are ignored.
   - If more than one distinct person fits: decide for each; if the decisions agree, use that decision, otherwise REVIEW / AMBIGUOUS_CUSTOMER_RECORD. customer_record_id = AMBIGUOUS.
3. **Resolve the hit** (P5):
   - DOB candidates from dobs, then age phrases in the first paragraph (age X at a date gives 2 years), then search results that match the listing phrase for the primary category AND a hit-tied country.
   - Country set: countries, nationality, citizenship, the last part of each address, and place_of_birth.
   - Earliest dated report: lines of the form "According to …; Month d, yyyy:".
   - Deceased date.
4. **Rules engine, in fixed order:**
   - Contradictions: TYPE → DECEASED → TEMPORAL (younger than 16 at the earliest report, or incorporated after it) → DOB (every hit DOB contradicted) → NAME (verified legal name vs all names and aliases) → LOCATION. A LOCATION mismatch is blocked when name and DOB agree; the hit goes to REVIEW.
   - Then agreement: a person with name+DOB (+country) gives SAME; a company with name+country gives SAME.
   - Otherwise REVIEW / INSUFFICIENT_DATA.
   - Fewer than 2 real name parts on an individual means the name can neither confirm nor clear the hit.
5. **Evidence and explanations:** each fact keeps its source record ID. Cite only the IDs behind the deciding fact plus the customer record(s). Build the 2–4 sentence reason from a template, optionally polished by an LLM, but it must quote this hit's own facts.
6. **LLM role (OpenRouter, temperature 0, fixed seed, cached by hash):**
   - Pull ages, dates and countries out of free-text profile notes, search snippets and residence notes.
   - Return structured JSON that is validated against regex fallbacks.
   - The LLM never makes the final decision, which keeps results reproducible. A run must take under 30 minutes.
7. **Safety rails:** any uncertainty in parsing, any conflict between sources, or any failure in extraction gives REVIEW, never DIFFERENT_ENTITY.

## Quality loop
- Score with `validate.py --score dev/practice_labels.jsonl`. Track unsafe clearances (target 0), the share of false positives cleared, clearance precision, same-entity recall and the review rate.
- Do error analysis on each practice label's `path`.
- Build synthetic edge tests for rules that are rare in dev: deceased, temporal, company incorporation, verified name vs nickname, document-number linkage, and ambiguity.
- Run 5 times to confirm the output is identical.

## Banking/regulatory framing (for the demo and the README, not scored)
- Maps to UK MLRs 2017 (CDD/EDD and PEP regs 35), OFSI asset-freeze obligations (Sanctions and Anti-Money Laundering Act 2018), and FCA SYSC 6.3 / Financial Crime Guide ch. 7 (sanctions screening systems and controls, documented rationale, QA).
- Gives an auditable decision log per hit (sources, rule fired, version, model and prompt hash), four-eyes review on all REVIEW and SAME results, and sample QA on clearances.
