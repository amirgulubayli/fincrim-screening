# Track 2: Round 2 review pack
Built 21 Nov 2026. Code: `/workspace/aml/system/` (`screen.py`; round 1 kept as `screen_round1.py`). Output: `output/dev.jsonl`. Nothing has been pushed to GitHub.

## Score on the 50 practice labels
| | Round 2 | Round 1 | Send everything to review |
|---|---|---|---|
| Decision score (cost saved, as in the brief) | **100%** | 16.7% | 0% |
| Cost (brief's matrix) | **0** | 90 | 108 |
| Decisions correct | **50 / 50** | 18 / 50 | 10 / 50 |
| Unsafe clearances | **0** | 0 | 0 |
| Right reason code / right customer record | 50 / 50 and 50 / 50 | 18 / 18 and 16 / 18 | — |

**Caveat.** 50/50 is on labels we can see, and I used them for error analysis while building. The real test is the about 550 unseen hits, worded differently. Expect lower there; the safety rules are what protect the score.

**Checks**
- **Same output every run:** two runs gave byte-identical output. A run takes about 9 seconds and uses no AI model.
- **Phone-group cross-check:** done on all 465 clearances (485 customer-person pairs). No other record sharing the customer's phone has a name *and* date of birth agreeing with the hit. **0 flags.**
- **Ozwald's round-1 calls are kept:** H-DFB2B9 is still SAME_ENTITY, and H-ADF453 is still REVIEW.

**Unsafe clearance caught during the build (not in the labels).** The first version read an age out of news-report text that wasn't about the person. Examples: "21 West Drive", "40 pounds", "18 AGL hand grenades". That produced 6 false DOB clearances on hits for Abdul Rahman, Omar Hussein, Abdur Rehman and others. Fixes:
- An age only counts if it's in a sentence that names the subject (or says he/she).
- Numbers followed by units or capitalised words are ignored.
- "According to …" report paragraphs are not mined for ages.

All 6 are no longer cleared on DOB. They are still cleared, but only on other policy grounds (location, verified name). They were rechecked in the phone-group cross-check.

## Decisions across all 819 hits
| Decision | Reason code | Round 2 | Round 1 |
|---|---|---|---|
| DIFFERENT_ENTITY | LOCATION_MISMATCH | 263 | — |
| DIFFERENT_ENTITY | DOB_MISMATCH | 94 | 25 |
| DIFFERENT_ENTITY | NAME_MISMATCH_VERIFIED | 81 | — |
| DIFFERENT_ENTITY | TEMPORAL_IMPOSSIBILITY | 22 | — |
| DIFFERENT_ENTITY | DECEASED | 4 | 4 |
| DIFFERENT_ENTITY | ENTITY_TYPE_MISMATCH | 1 | — |
| SAME_ENTITY | NAME_DOB_LOCATION_MATCH | 80 | 35 |
| SAME_ENTITY | NAME_LOCATION_MATCH | 39 | 22 |
| SAME_ENTITY | NAME_DOB_MATCH | 4 | 2 |
| REVIEW | INSUFFICIENT_DATA | 216 | 731 |
| REVIEW | AMBIGUOUS_CUSTOMER_RECORD | 15 | — |
| **Totals** | | **465 cleared / 123 confirmed / 231 review** | 29 / 59 / 731 |

- **Alerts:** **176 of 440 alerts fully cleared** (19 in round 1). 264 stay open.
- **Unlinked alerts (146):** 119 resolved to one person, 27 matched more than one person, 0 found no match.
- **More than one person fitting (35 hits):**
  - 15 go to REVIEW / AMBIGUOUS_CUSTOMER_RECORD because the candidates lead to different outcomes.
  - 8 stay REVIEW / INSUFFICIENT_DATA because every candidate leads to review.
  - 9 are cleared and 3 confirmed, because every candidate person led to the same decision; this is what the policy says to do.
- **Where hit dates of birth now come from:** the DOB field (152 hits), profile notes (50), and corroborated web search results (69). 548 hits still have no date of birth.

## 10 example decisions (new rules)
1. **H-13E1CF**: DIFFERENT / TEMPORAL_IMPOSSIBILITY. "The customer was born 2007-08-21 (KYC-358C76), so they were younger than 16 at the hit's earliest dated report on 2020-01-08…" *Label: DIFFERENT (TEMPORAL or LOCATION). Agrees; this was REVIEW in round 1.*
2. **H-0F6562**: DIFFERENT / TEMPORAL_IMPOSSIBILITY. "Born 1996-05-04 (NOTE-C25448), younger than 16 at the earliest dated report on 2008-01-26." *Not labelled.*
3. **H-9FD80D**: DIFFERENT / LOCATION_MISMATCH. "The customer is linked only to Germany … the hit Markus A. Müller-Kägi is linked only to Switzerland." *Label: DIFFERENT / LOCATION_MISMATCH. Agrees.*
4. **H-B1018D**: DIFFERENT / LOCATION_MISMATCH. "Customer linked only to Serbia; hit Jovanović, Dejan linked only to Montenegro." *Not labelled. Needs Ozwald's judgement (see below).*
5. **H-2808BE**: DIFFERENT / NAME_MISMATCH_VERIFIED. "Verified legal name CHINEDU AZEEZ (KYC-20EE17) does not agree with the hit's name Ridwan, Azeez." *Label: DIFFERENT / NAME_MISMATCH_VERIFIED. Agrees.*
6. **H-F96284**: DIFFERENT / NAME_MISMATCH_VERIFIED. "Verified legal name Ellie Hadiza Babic (KYC-31BA4C) does not agree with Babić, Boris." Only the surname is shared. *Not labelled. Needs Ozwald's judgement.*
7. **H-5EEC5A**: DIFFERENT / ENTITY_TYPE_MISMATCH. "The customer (CUS-559377) is a company but the hit Mun Chen is an individual." *Not labelled.*
8. **H-85CA6C**: DIFFERENT / DOB_MISMATCH. "Customer DOB 1976-08-05 (KYC-8DDC26) contradicts every birth date the hit Corral Bustamante, David gives (1972 or 1973) (web search results: SR-6F9E9A)." The search result is used only because it matches the listing type and a country tied to the hit. *Not labelled.*
9. **H-D861DA**: DIFFERENT / DOB_MISMATCH. "Customer DOB 1979-11-08 (NOTE-8548C1) contradicts the hit's 1976 or 1977 (from the hit's profile notes: 'he was 46' in January 2023)." *Label: DIFFERENT / DOB_MISMATCH. Agrees.*
10. **H-D640B5**: REVIEW / AMBIGUOUS_CUSTOMER_RECORD. "2 different people fit the alert … CUS-146456: DIFFERENT (LOCATION_MISMATCH); CUS-523173: REVIEW." *Not labelled.* Also **H-6C7985** (labelled): 2 people fit and both lead to REVIEW, so the result is REVIEW / INSUFFICIENT_DATA with customer AMBIGUOUS. Agrees.

## Calls needing Ozwald's judgement
1. **Surname-only overlap with a verified name clears the hit** (e.g. H-F96284, Babic vs Babić, Boris). The policy's name test needs 2 matching parts, or every part of the shorter name, so one shared surname is a mismatch. That is literal policy, but it's the boldest clearance type, with 81 hits. Option: send to REVIEW when the surname matches but the given name doesn't. This is safer, but each wrong review costs 3.
2. **Location clearance between neighbouring or formerly unified countries** (Serbia/Montenegro, Kosovo and similar). Policy: "territories count as their own country", and every country must differ, so we clear. There are 263 location clearances in total. Does he want a "related countries" carve-out sent to REVIEW?
3. **A sole country link counts as fully tied.** If a hit's only country is a business address, it still counts. That is literal policy.
4. **When several people fit and all lead to the same clearance** (9 hits), we clear with customer = AMBIGUOUS, as the policy says. Comfortable?
5. **Ages inside "According to …" report paragraphs are ignored.** This is conservative: some real ages may be missed, so those hits go to REVIEW rather than being cleared. Round 3 could use an AI model to read these safely.
6. **The location rule uses every country a note mentions for that customer** (where they lived, transfers and so on). This is deliberately conservative: more customer countries means fewer location clearances.

## Remaining gaps (round 3)
- **Wording on the unseen set:** age and DOB phrasing will differ. Add an optional AI model step (OpenRouter, temperature 0, cached) to extract ages, dates and countries from free text. The rules still decide, and any disagreement goes to REVIEW.
- **Evidence trimming:** location clearances cite the customer record but not always the identity check that gave the country, and REVIEW decisions cite the minimum. Align with the label style (the deciding check or note IDs).
- **Hits with no date of birth found:** 548, mostly news-style "According to …" profiles. Search-result corroboration only covers some names.
- **README:** architecture diagram, how both sides are resolved, evidence model, determinism, 3 worked cases, time (about 9 seconds) and cost (£0, no model).
- **Edge-case tests:** territories, companies incorporated after a report, document-number merges, placeholder names, and partial dates on both sides.
- **Real data:** run against the official `dev/` JSONL and `validate.py` when available. Our `dev/` is still exported from the workbook, where long text is cut at 32,000 characters (2 hits).
