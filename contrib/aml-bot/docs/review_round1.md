# Track 2: Round 1 review pack
Built 21 Nov 2026, ready about 12:20 London time. Code: `/workspace/aml/system/` (run with `system/run dev output/dev.jsonl`). Nothing has been pushed to GitHub.

## Headline
| Measure (50 practice-labelled hits) | Round 1 | "Send everything to review" baseline |
|---|---|---|
| Decision score (cost saved vs baseline, as in the brief) | **16.7%** | 0% |
| Cost (brief's matrix) | 90 | 108 |
| Decisions correct | 18 / 50 (36%) | 10 / 50 |
| **Unsafe clearances** (a real or uncertain match cleared) | **0** | 0 |
| Right decision also has a fitting reason code | 18 / 18 | — |
| Right decision also has the right customer record | 16 / 18 (the 2 misses are unlinked alerts that round 1 does not search) | — |

- **Determinism:** two runs gave byte-identical output. The run takes about 2 seconds and uses no language model.
- **Unsafe clearances:** one appeared in the first draft. Hit **H-4D76E3** (Mohammad Nadeem Ghani) was cleared on DOB because the customer record said 1963-12-02. The bank's approved identity check, written up as an onboarding note (NOTE-4E8A14), says 1968-02-17, which matches the hit. I made two fixes:
  - (a) Identity checks recorded in notes now count as verified ID, and the most recent approved check wins.
  - (b) **Safety rail:** a hit is only cleared on DOB if *every* DOB the bank holds for the customer contradicts it.
  
  That hit is now correctly SAME_ENTITY. I also checked all 29 clearances against other records sharing the customer's phone number. None had an agreeing DOB.

## What it decided across all 819 hits
| Decision | Reason code | Hits |
|---|---|---|
| REVIEW | INSUFFICIENT_DATA | 731 (89%) |
| SAME_ENTITY | NAME_DOB_LOCATION_MATCH | 35 |
| SAME_ENTITY | NAME_LOCATION_MATCH (companies) | 22 |
| SAME_ENTITY | NAME_DOB_MATCH | 2 |
| DIFFERENT_ENTITY | DOB_MISMATCH | 25 |
| DIFFERENT_ENTITY | DECEASED | 4 |

In total that is 29 cleared, 59 confirmed and 731 sent to review. 19 of the 440 alerts are fully cleared. 262 hits sit in the 146 unlinked alerts and all go to REVIEW for now.

## 10 example decisions
1. **H-58A39C**: DIFFERENT_ENTITY / DOB_MISMATCH. "The customer's date of birth 1965-03-28 (KYC-232AE0) contradicts every birth date the hit Timothy Pangelinan Villagomez gives (1962-09-10)." *Label: DIFFERENT_ENTITY / DOB_MISMATCH. Agrees.*
2. **H-5E39F0**: DIFFERENT_ENTITY / DOB_MISMATCH. "The customer's date of birth 1988-10-03 (NOTE-302C97) contradicts every birth date the hit Ramirez Diaz, Jesus Amable gives (1963-12-16)." *Label: DIFFERENT_ENTITY (DOB_MISMATCH or NAME_MISMATCH_VERIFIED). Agrees.*
3. **H-BD709E**: DIFFERENT_ENTITY / DECEASED. "The hit's profile records Jonathan Reyes as deceased on 2022-03, before the customer (CUS-343316) was screened on 2026-11-14." *Not labelled.*
4. **H-4D76E3**: SAME_ENTITY / NAME_DOB_LOCATION_MATCH. "The customer's date of birth 1968-02-17 (NOTE-4E8A14) agrees with the hit's 1968-02. Both … linked to United Kingdom. The customer's name agrees with … Mohammad Nadeem Ghani." *Label: SAME_ENTITY. Agrees. This was the hit unsafely cleared in the first draft.*
5. **H-A72A50**: SAME_ENTITY / NAME_DOB_LOCATION_MATCH. "DOB 1962-10-23 (KYC-6075EB) agrees with the hit's 1961 or 1962. Both … United States. Name agrees with Bowman, David." *Label: SAME_ENTITY. Agrees. The label also lists a second record for the same person (CUS-977888); merging records like this comes in round 2.*
6. **H-02115F**: SAME_ENTITY / NAME_LOCATION_MATCH. "Both the customer company and the hit are linked to Spain. The customer's name agrees with … Pescanova, Sociedad Anónima." *Label: SAME_ENTITY. Agrees.*
7. **H-DFB2B9**: SAME_ENTITY / NAME_DOB_MATCH. "DOB 1988-04-28 (NOTE-03FC88) agrees with the hit's 1987 or 1988. Name agrees with Singh, Harwinder." The hit gives no country. *Not labelled. Worth a look: Harwinder Singh is a very common name, but the policy allows the match when name and DOB agree and nothing contradicts them.*
8. **H-FF3096**: REVIEW / INSUFFICIENT_DATA. "Name agrees with Abdul Rahman Yasin. The hit gives no date of birth, so it cannot be compared." *Label: REVIEW. Agrees.*
9. **H-13E1CF**: REVIEW / INSUFFICIENT_DATA. "Name agrees with Abdul Rahman. The hit gives no date of birth." *Label: **DIFFERENT_ENTITY** (customer born 2007, so at most 13 at a 2020 report, and Singapore/US vs Pakistan). Safe, but a missed clearance; the "too young" and country rules come in round 2.*
10. **H-ADF453**: REVIEW / INSUFFICIENT_DATA. "Name Marcus Miller and DOB 1979-08-14 (KYC-A932A4) agree with the hit (1979 or 1980), but the customer is linked to Germany and the hit to United States. A country difference does not clear a name+DOB match." *Not labelled. This follows the SOP rule exactly.*

## Where the practice-label misses come from
- **26 true clearances sent to REVIEW (cost 3 each).** Mostly rules not in round 1: LOCATION_MISMATCH (17 labels), NAME_MISMATCH_VERIFIED (7), TEMPORAL_IMPOSSIBILITY (1), plus unlinked alerts.
- **6 true matches sent to REVIEW (cost 2 each).** 4 need the hit's DOB from its profile text ("a 30-year-old in Nov 1998") or from web search results, and 2 are unlinked company alerts.
- **No wrong confirmations and no unsafe clearances.**

## Known gaps: round 2 (scoring impact)
1. **Location mismatch clearance.** Countries from all the customer's records, the approved ID document, and places the notes say they lived, against the hit's countries. Blocked when name and DOB agree.
2. **Verified-name mismatch clearance (NAME_MISMATCH_VERIFIED).** Already detected; round 1 deliberately sends these to review.
3. **Unlinked alerts (146 alerts / 262 hits).** Search the system of record on name, DOB, country and city. If several people fit and they lead to different decisions, the result is AMBIGUOUS_CUSTOMER_RECORD.
4. **Same person across records.** Merge on same phone + same DOB, or the same approved document number.
5. **Hit DOB from free text.** Ages in the first paragraph of the profile give two possible birth years. Web search results count only when they match the listing type and a country tied to the hit.
6. **"Too young at the time" test.** Uses the dated report lines ("According to …; Month d, yyyy:").

## Round 3
- An optional language model for wording variations, kept deterministic with caching and falling back to REVIEW on any doubt.
- Tighter evidence lists: REVIEW reasons sometimes cite a DOB source the text doesn't mention.
- README with diagram, worked cases, run time and cost.
- Edge-case tests.
- Check against the official validate.py once the dev/ data room is available.

## Assumptions
- `dev/` was exported from the workbook because we don't have the official JSONL. The file names follow the brief. Fields are the workbook columns. hit_ids, reason_codes, customer_record_ids and evidence are lists. Hit multi-value fields (aliases, dobs, countries, addresses) are kept as "; "-separated strings, and the loader accepts either form.
- Long texts are cut at 32,000 characters in the workbook (this affects 2 hits).
- `validate_local.py` is my stand-in for the organisers' validate.py and uses the brief's cost matrix.
