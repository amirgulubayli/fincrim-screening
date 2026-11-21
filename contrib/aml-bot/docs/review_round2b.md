# Track 2: Round 2b review pack (Ozwald's policy decisions applied)
21 Nov 2026. Code: `/workspace/aml/system/screen.py`. Output: `output/dev.jsonl`. Nothing has been pushed to GitHub.

## Decisions applied
1. **One shared name part with the verified legal name:** REVIEW instead of clearing, with guidance for the reviewer.
2. **Location "mismatch" between countries that were once one state:** REVIEW with guidance. Groups: former Yugoslavia and Serbia & Montenegro (RS, ME, XK, BA, HR, SI, MK); former USSR (15 states); Czechoslovakia (CZ, SK); Sudan/South Sudan; Ethiopia/Eritrea. Neighbours that were never unified still clear.
3. **Hit whose only country link is a business address:** no location clearance; REVIEW with guidance. Applied to individuals only, because for a company the registered or business address *is* its location.
4. **Unchanged:** several people fit and all lead to clearance (keep clearing); ages inside news paragraphs are ignored; every country mentioned in the customer's notes counts.

The brief's REVIEW codes are only INSUFFICIENT_DATA and AMBIGUOUS_CUSTOMER_RECORD, so the new reviews use **INSUFFICIENT_DATA**. The guidance goes in the reason text, which starts "Escalated, not cleared:" and ends "Reviewer: …".

## Hits moved
| Change | Hits moved | From → to |
|---|---|---|
| 1 One shared name part | **80** | 74 cleared (NAME_MISMATCH_VERIFIED) → REVIEW; 6 REVIEW (AMBIGUOUS_CUSTOMER_RECORD) → REVIEW (INSUFFICIENT_DATA), because every candidate person now leads to review |
| 2 Former single state | **2** | cleared (LOCATION_MISMATCH) → REVIEW (H-B1018D Serbia/Montenegro; H-6DACC0 Serbia vs Croatia/Slovenia) |
| 3 Business address only | **0 in dev** | No dev hit has a business address as its only country link; every hit with a business address also has another country field or tag. Tested on a synthetic case: correctly REVIEW. It will apply on the unseen set |
| Knock-on | 1 | H-35632C: REVIEW (AMBIGUOUS_CUSTOMER_RECORD) → REVIEW (INSUFFICIENT_DATA), because both candidate people now lead to review |

## New decision distribution (819 hits)
| Decision | Reason code | Round 2b | Round 2 |
|---|---|---|---|
| DIFFERENT_ENTITY | LOCATION_MISMATCH | 261 | 263 |
| DIFFERENT_ENTITY | DOB_MISMATCH | 94 | 94 |
| DIFFERENT_ENTITY | TEMPORAL_IMPOSSIBILITY | 22 | 22 |
| DIFFERENT_ENTITY | NAME_MISMATCH_VERIFIED | 7 | 81 |
| DIFFERENT_ENTITY | DECEASED | 4 | 4 |
| DIFFERENT_ENTITY | ENTITY_TYPE_MISMATCH | 1 | 1 |
| SAME_ENTITY | NAME_DOB_LOCATION_MATCH | 80 | 80 |
| SAME_ENTITY | NAME_LOCATION_MATCH | 39 | 39 |
| SAME_ENTITY | NAME_DOB_MATCH | 4 | 4 |
| REVIEW | INSUFFICIENT_DATA | 299 | 216 |
| REVIEW | AMBIGUOUS_CUSTOMER_RECORD | 8 | 15 |
| **Totals** | | **389 cleared / 123 confirmed / 307 review** | 465 / 123 / 231 |

Alerts fully cleared: **131 of 440** (was 176).

## Score on the 50 practice labels
| | Round 2b | Round 2 | Round 1 | All REVIEW |
|---|---|---|---|---|
| Decision score (cost saved) | **91.7%** | 100% | 16.7% | 0% |
| Cost | 9 | 0 | 90 | 108 |
| Correct | 47/50 | 50/50 | 18/50 | 10/50 |
| Unsafe clearances | **0** | 0 | 0 | 0 |

- **Labels that changed (all because of rule 1):** **H-2808BE** (CHINEDU AZEEZ vs Ridwan, Azeez), **H-380854** (SARAH JOHNSON vs Johnson, James Jacob) and **H-EDBB79** (TASLIMA RAHIM ISLAM vs Islam, Md Saidul).
  - The reference answer for all three is DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED. We now say REVIEW, at a cost of 3 each.
  - This is the deliberate price of Ozwald's more cautious rule. On the brief's scoring, every such hit that really is a different person costs 3 points, and the safety benefit isn't scored.
  - Note that 2 of the 3 have different first names *and* sexes (Sarah vs James Jacob; Taslima vs Md Saidul). A middle option would be to clear only when the given names clearly differ, and still escalate initials or missing given names. That would win most of these back.
- **Checks:** no other labels changed. Reason code and customer record are right on all 47 correct decisions.
- **Same output every run:** two runs gave byte-identical output.
- **Safety:** clearances only went down (465 to 389, with no new clearances), so the earlier phone-group cross-check (0 flags) still covers every remaining clearance.

## Example reason text for each new review type
1. **One shared name part (H-73AB0E):** "Escalated, not cleared: the customer's verified legal name INGRID NILS LUCIA OLIVEIRA (KYC-6C6E04) shares only one name part, 'Oliveira', with the listed name Oliveira, Marcelo. Policy needs two matching name parts to treat the names as agreeing, and a single shared name part (usually the surname) is too weak to clear safely. Reviewer: compare the given names, date of birth and nationality against the listing."
2. **Former single state (H-B1018D):** "Escalated, not cleared: the customer is linked to Serbia and the hit Jovanović, Dejan to Montenegro. These countries were part of one state, Yugoslavia (and Serbia and Montenegro), so listing data recorded before the split may name either country for the same person. Reviewer: check the dates of the listing's country information, place of birth and nationality before treating this as a location mismatch."
3. **Business address only (synthetic test, built from H-988D1E):** "Escalated, not cleared: the customer is linked to Singapore, United States, and the hit ABDUL RAHMAN's only country link is a business address in Pakistan. A business address shows where the person works or registered a company, not where they live or their nationality, so it does not support a location clearance. Reviewer: check the listing for nationality, residence and date of birth and compare them with the customer's verified identity."

## Notes and simplifications
- **Former-state rule ignores dates:** it triggers whenever the countries share a former state, not only when the hit's information pre-dates the split. Listing data rarely carries reliable dates per country field, so this is the cautious reading.
- **Rule 1 trigger:** it fires when any verified name shares exactly one name part with any of the hit's names or aliases. Where no part is shared at all, the hit is still cleared as NAME_MISMATCH_VERIFIED (7 hits).
