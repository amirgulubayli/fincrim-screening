# Track 2: Round 3 review pack
21 Nov 2026. Code: `/workspace/aml/system/`. Output: `output/dev.jsonl`. Nothing has been pushed to GitHub.

## Headline
| On the 50 practice labels | Round 3 | 2c | 2b | 2 | 1 | All REVIEW |
|---|---|---|---|---|---|---|
| Decision score (cost saved) | **97.2%** | 97.2% | 91.7% | 100% | 16.7% | 0% |
| Cost | 3 | 3 | 9 | 0 | 90 | 108 |
| Correct | 49/50 | 49/50 | 47/50 | 50/50 | 18/50 | 10/50 |
| Unsafe clearances | **0** | 0 | 0 | 0 | 0 | 0 |
| Evidence IDs identical to the reference (correct decisions) | **48/49** | 12/49 | — | — | — | — |

- **Reasons:** right reason code and customer record on 49/49 correct decisions.
- **The one miss is still H-2808BE** (CHINEDU AZEEZ vs "Ridwan, Azeez"). It is REVIEW against a DIFFERENT_ENTITY label because the name may be reordered, which is Ozwald's cautious rule.
- **Same output every run:** 5 runs plus the main output gave 6 identical files (one MD5).
- **A run takes about 7 seconds and costs £0.** There is no language model.
- **No new clearances this round** (clearances went from 436 to 430), so the earlier phone-group cross-checks (0 flags) still cover every clearance.

## (a) Surnames that are commonly a father's given name: 6 hits moved
All 6 moved from DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED to REVIEW / INSUFFICIENT_DATA:
- H-02F3CB (IBRAHIM UMAR vs Umar, Mohammad)
- H-500CDC (JOSEPH AHMED vs Ahmed, Suleiman)
- H-A1E3EC (YUSUF ABUBAKAR vs Abubakar, Suleiman Bello)
- H-CFD570 (YUSUF ABUBAKAR vs Abubakar, Suleiman)
- H-DB4E83 (ABUBAKAR OBINNA UMAR vs Umar, Garba Baba)
- H-FE949E (OBINNA ALHASSAN vs Alhassan, Abdul Rahaman)

These are all six IDs from the round-2c borderline list; "the 7th" was the second Abubakar hit, which is already included. The rule is general: a list of about 100 such surnames, plus their variants from `name_variants.json`.

Example reason: "Escalated, not cleared: the customer's verified legal name IBRAHIM UMAR (KYC-DA4324) shares only one name part, 'Umar', with the listed name Umar, Mohammad. Policy needs two matching name parts … too weak to clear safely, and the shared surname Umar is commonly a father's given name in Arabic, Hausa and West African naming traditions, so the order of name parts is unreliable. Reviewer: compare the given names, date of birth and nationality against the listing."

## Decision distribution (819 hits)
| Decision | Reason code | Round 3 | 2c |
|---|---|---|---|
| DIFFERENT_ENTITY | LOCATION_MISMATCH | 261 | 261 |
| DIFFERENT_ENTITY | DOB_MISMATCH | 94 | 94 |
| DIFFERENT_ENTITY | NAME_MISMATCH_VERIFIED | 48 | 54 |
| DIFFERENT_ENTITY | TEMPORAL_IMPOSSIBILITY | 22 | 22 |
| DIFFERENT_ENTITY | DECEASED | 4 | 4 |
| DIFFERENT_ENTITY | ENTITY_TYPE_MISMATCH | 1 | 1 |
| SAME_ENTITY | NAME_DOB_LOCATION_MATCH | 80 | 80 |
| SAME_ENTITY | NAME_LOCATION_MATCH | 39 | 39 |
| SAME_ENTITY | NAME_DOB_MATCH | 4 | 4 |
| REVIEW | INSUFFICIENT_DATA | 254 | 248 |
| REVIEW | AMBIGUOUS_CUSTOMER_RECORD | 12 | 12 |
| **Totals** | | **430 / 123 / 266** | 436 / 123 / 260 |

Totals are cleared / confirmed / review. Alerts fully cleared: **155 of 440**.

## (b) Round 3 work
- **Tighter evidence.** Each decision now cites only what decided it:
  - DOB and verified-name clearances: the identity check or note, plus any search result.
  - Location clearances: the person's records, the verified check, and every note that placed them in a country.
  - Reviews: the records tying the customer to the shared country.
  
  Agreement with the reference evidence went from 12 to 48 of 49.
- **Edge-case tests:** `system/tests/test_rules.py`, **19 tests, all pass**. Run with `python3 -m unittest discover -s system/tests -v`.
  - **Rules:** dates, countries, DOB rules, names (variants, joined forms, placeholders, legal forms), KYC-note parsing, and the age extractor ignoring units.
  - **Rules (continued):** entity type, deceased, too young, DOB mismatch (including the verified-DOB lesson), location mismatch plus the name+DOB block, a note country blocking a clearance, business-address-only, former single state (plus a never-unified neighbour still clearing), the surname middle rule (5 variants), patronymic surnames, company match, ambiguous unlinked customers, and the same-person merge.
- **No language model added.** It wasn't needed for the score and would add run-to-run variation. The README describes how it could be added later (extractor only, off by default).

## README
Path: `/workspace/aml/system/README.md`. In five lines:
1. A deterministic, standard-library-only rules engine (no language model) that resolves the customer (link, or search, then a merged "one person" with a verified identity) and the listed entity (names, countries with address tags, DOB from field, profile text or corroborated search results), then applies POLICY.md in order.
2. Every decision cites only its deciding records. The output is byte-identical over 5 runs, and a run takes about 7 seconds at £0.
3. Three worked cases from real output: H-58A39C (DIFFERENT, DOB), H-4D76E3 (SAME; the early unsafe-clearance lesson) and H-B1018D (REVIEW, former single state).
4. All 9 policy calls by the Head of Financial Crime are listed. Every escalation created by them carries "Escalated, not cleared: … Reviewer: …" guidance.
5. UK framing: MLRs 2017 (regs 27–28, 33–35), OFSI/SAMLA, FCA SYSC 6.3 and FCG. Plus limitations (regex free text, 548 hits with no DOB, the former-state rule ignoring dates) and the data caveat (dev/ was built from the workbook).

## Left before submission
1. **Official data:** get the event's `dev/` JSONL and `validate.py`. Re-run, re-score and fix any format differences. Our `dev/` is exported from the workbook, so field types (e.g. list vs "; " string) may differ, although the loader accepts both.
2. **Repo:** create a public GitHub repo containing only `system/` and `output/`. No `dev/` data and no keys, as the brief requires. Push the final commit before the 17:00 code freeze, then submit the link and project name on the event portal. All of this needs Ozwald's go-ahead and account.
3. **Demo video:** the brief asks for a working prototype plus a video demo at code freeze. If he wants a short walkthrough, it could use the three README cases and the review packs.
4. **Optional:** a quick check by Ozwald of a sample of the 48 remaining NAME_MISMATCH_VERIFIED and 261 LOCATION_MISMATCH clearances.
