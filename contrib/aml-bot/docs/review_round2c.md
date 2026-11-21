# Track 2: Round 2c review pack (middle rule for surname-only overlap)
21 Nov 2026. Code: `/workspace/aml/system/screen.py` (function `given_names_differ`). Output: `output/dev.jsonl`. Nothing has been pushed to GitHub. Round 3 waits for Ozwald's go.

## The rule as built
A hit whose verified legal name shares exactly one name part with the listed name is **cleared** (DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED) only if *all* of these hold for every verified name and every hit name or alias that shares a part:
- The shared part is the **surname on both sides**: the last part of the customer's verified name, and the part before the comma (or the last part) of the listed name.
- **No initials** (single letters) appear on either side.
- **Both sides have at least one full given name** (3 or more letters).
- **No given-name pair is compatible.** Compatible means:
  - the same name or within the policy's small spelling-edit allowance;
  - in the same group of the brief's `name_variants.json` (Mohammed/Muhammad, Bill/William, Alex/Aleksandr…);
  - one name is the start of the other (Alex/Alexander);
  - joined/split forms (Abdul Rahman/Abdulrahman);
  - a 2-letter abbreviation with the same first letter (Md/Mohammed).

Anything else stays **REVIEW** with the "Escalated, not cleared:" guidance, now with the specific reason for doubt added.

## Hits moved (from round 2b)
| Move | Hits |
|---|---|
| REVIEW → **DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED** (given names clearly differ) | **47** |
| REVIEW / INSUFFICIENT_DATA → REVIEW / AMBIGUOUS_CUSTOMER_RECORD (two candidate people now lead to different outcomes) | 4 |

**Still under review for single-name-part overlap: 30 hits.**
- 15: the shared part is not the listed person's surname (the name may be reordered).
- 11: the shared part is not the customer's surname.
- 3: there are initials.
- 1: the given names are abbreviations only.

## New decision distribution (819 hits)
| Decision | Reason code | 2c | 2b | 2 |
|---|---|---|---|---|
| DIFFERENT_ENTITY | LOCATION_MISMATCH | 261 | 261 | 263 |
| DIFFERENT_ENTITY | DOB_MISMATCH | 94 | 94 | 94 |
| DIFFERENT_ENTITY | NAME_MISMATCH_VERIFIED | **54** | 7 | 81 |
| DIFFERENT_ENTITY | TEMPORAL_IMPOSSIBILITY | 22 | 22 | 22 |
| DIFFERENT_ENTITY | DECEASED | 4 | 4 | 4 |
| DIFFERENT_ENTITY | ENTITY_TYPE_MISMATCH | 1 | 1 | 1 |
| SAME_ENTITY | NAME_DOB_LOCATION_MATCH | 80 | 80 | 80 |
| SAME_ENTITY | NAME_LOCATION_MATCH | 39 | 39 | 39 |
| SAME_ENTITY | NAME_DOB_MATCH | 4 | 4 | 4 |
| REVIEW | INSUFFICIENT_DATA | 248 | 299 | 216 |
| REVIEW | AMBIGUOUS_CUSTOMER_RECORD | 12 | 8 | 15 |
| **Totals** | | **436 / 123 / 260** | 389 / 123 / 307 | 465 / 123 / 231 |

Totals are cleared / confirmed / review. Alerts fully cleared: **157 of 440** (2b: 131; round 2: 176).

## Score on the 50 practice labels
| | 2c | 2b | 2 | 1 | All REVIEW |
|---|---|---|---|---|---|
| Decision score (cost saved) | **97.2%** | 91.7% | 100% | 16.7% | 0% |
| Cost | 3 | 9 | 0 | 90 | 108 |
| Correct | 49/50 | 47/50 | 50/50 | 18/50 | 10/50 |
| Unsafe clearances | **0** | 0 | 0 | 0 | 0 |

- **Labels that changed:** H-380854 (Sarah vs James Jacob Johnson) and H-EDBB79 (Taslima Rahim vs Md Saidul Islam) are cleared again, which matches their labels.
- **Still REVIEW against a DIFFERENT_ENTITY label: H-2808BE**, CHINEDU AZEEZ vs "Ridwan, Azeez". Azeez is the customer's surname but the listed person's *given* name, so the name may be reordered. That is doubt, so it goes to REVIEW, at a cost of 3.
- **Reasons:** right reason code and customer record on all 49 correct decisions.
- **Same output every run:** two runs gave byte-identical output.
- **Phone-group cross-check on the 47 new clearances:** no other record sharing the customer's phone has a name agreeing with the hit. **0 flags.**

## Example reasons
- **Cleared (H-380854):** "The customer's verified legal name SARAH JOHNSON (KYC-2EA887) shares only the surname 'Johnson' with the listed name Johnson, James Jacob. The given names clearly differ: Sarah versus James Jacob, with no variant, initial or nickname link between them. The verified name therefore does not agree with any of the hit's names or aliases." Evidence: CUS-160323, CUS-485845 (one person across two records), KYC-2EA887.
- **Still reviewed (H-6359AE):** "Escalated, not cleared: the customer's verified legal name LUCIA ROZI (KYC-BAB7A1) shares only one name part, 'Rozi', with the listed name Rozi, Farizatul Aifah B Fahrur. Policy needs two matching name parts to treat the names as agreeing, and a single shared name part (usually the surname) is too weak to clear safely, and one of the names contains initials. Reviewer: compare the given names, date of birth and nationality against the listing."

## Borderline clearances for Ozwald to eyeball
1. **Surname matched only through a spelling edit:** H-41D303, MICHAEL COTA vs COSTA, MARCOS ROBERTO. Cota/Costa is within the policy's 1-edit allowance and the given names differ (Michael vs Marcos). This rests on a fuzzy surname plus different given names.
2. **Cultures where the "surname" is a father's given name** (Hausa/Arabic naming), so the order can vary between documents:
   - H-02F3CB: IBRAHIM UMAR vs Umar, Mohammad.
   - H-500CDC: JOSEPH AHMED vs Ahmed, Suleiman.
   - H-A1E3EC and H-CFD570: YUSUF ABUBAKAR vs Abubakar, Suleiman (Bello).
   - H-DB4E83: ABUBAKAR OBINNA UMAR vs Umar, Garba Baba.
   - H-FE949E: OBINNA ALHASSAN vs Alhassan, Abdul Rahaman.
   
   The given names do differ, but he may prefer REVIEW whenever the surname is itself a common given name (Umar, Ahmed, Abubakar, Ibrahim…).
3. **Similar-looking given names that aren't variants:** H-E43D3C and H-7ED0E6, DANIEL SARGSYAN vs Sargsyan, Davit (Vahan) / Tigran Davit. Daniel ≠ Davit (David), but they look close.
4. **Many hits cleared on one customer's name:** for example TASLIMA RAHIM ISLAM gives 8 hits for Saidul / Md Saidul Islam, and Liam Mensah and Amelia Owusu give 3 each. Correct by the rule, but a single bad KYC name would affect all of them.
