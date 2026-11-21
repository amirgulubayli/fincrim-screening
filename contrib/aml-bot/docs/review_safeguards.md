# Round: three safeguards (attacker test, many-to-many context, explain-or-escalate)

## Decision changes vs the previous output/dev.jsonl
**11 hits changed:** 10 clearances became REVIEW, and 1 REVIEW changed reason code.
- **The 10 former clearances (DIFFERENT_ENTITY/LOCATION_MISMATCH → REVIEW):** H-373F2A, H-15E86F, H-AC7362, H-7BA6A4, H-AF3F4D, H-6AD21E, H-61DF7B, H-4D46BD, H-4C9EE5, H-D18FF4.
  - **Why they changed:** in each, the names share one name part, the DOB agrees, and the countries differ. Examples: customer DAVID KIM vs "Kim, Jong Sik"; Stephanie Cheema vs "Cheema, Azam".
  - **Why that blocks clearance:** a married surname, a dropped middle name or a different spelling could explain the name difference, so a country difference no longer clears.
  - **Likely cost:** most are probably different people, about 3 points each if so. This extends Ozwald's call 2 ("a location difference never clears a name+DOB match").
- **The reason-code change:** H-9AD76C moved from REVIEW/AMBIGUOUS_CUSTOMER_RECORD to REVIEW/INSUFFICIENT_DATA. Both people who fit the alert now lead to REVIEW.
- **Reason text only:** 109 other REVIEW reasons changed wording, with no decision change, because of the common-name context.
- **New totals:** DIFFERENT_ENTITY 420, SAME_ENTITY 123, REVIEW 276. 147 of 440 alerts fully cleared (was 155).
- **Labels:** 49/50 (97.2%), 0 unsafe.

## 1. Attacker test (system/adversary.py)
- **Targets:** 399 hits not cleared, including the 12 labelled true matches. 355 of them are plausible true matches: SAME, labelled SAME, or the names already agree.
- **Attacks:** 3,433 rewrites (transliteration, order swap, dropped middle name, married surname, US date order, year-only DOB, typo, former-state/demonym/ISO country, accents, hyphens/spaces).
- **Before the fixes: 24 plausible true matches cleared.** Breakdown:
  - married surname: 13
  - dropped middle name: 6
  - typo: 5
  - The attacks also exposed one more gap: a web search result keyed on a differently written query was not found.
- **Fixes (all generic):**
  - An ambiguous dd/mm vs mm/dd date counts as a contradiction only if both readings contradict.
  - A wider, blocking-only name link: letter swaps, short-name typos and a romanisation key.
  - A shared name part plus an agreeing DOB blocks a location clearance.
  - Search results are indexed on normalised names.
- **After the fixes: 5.** All five are a dropped middle name where the customer is known by that middle name (e.g. verified "David Taylor" vs list "Michael Taylor"). They clear through Ozwald's surname rule, so they are left for his decision.
- **Rewrites of reviews where the names never agreed:** 21 clear. Example: IBRAHIM UMAR vs "Mohammad Silva". The rewritten name genuinely does not match, and the SOP clears on that.

## 2. Many-to-many
**Customers hit per listed name:**

| Customers hit | Listed names |
|---|---|
| 1 | 258 |
| 2 | 138 |
| 3 | 24 |
| 4 | 5 |
| 5 | 3 |

Top names: "Abdul Rahman", "Muhammad Nadeem" and "Abdul Rahman Yasin" (5 customers each), then "Christopher George" (4).

**Listed names per customer:** 332 customers hit 1 listed name, 38 hit 2, and 46 hit 3 or more (up to 15).

**Use:** context was added to 114 REVIEW reasons, inside the existing last sentence. It is never used to clear.

## 3. Explain or escalate
- **Every DIFFERENT_ENTITY and SAME_ENTITY decision gets a one-sentence audit reason** (543 on dev). Set `SCREEN_EXPLAIN=<file>` to write them.
- **The gate:** a clearance without concrete values from a trusted record, or one citing an ID that does not exist, becomes REVIEW.
- **Result on dev:** 0 downgrades. Every clearance could be stated.

## Checks
- **Tests:** 55, all passing.
- **Determinism:** 5 runs identical (MD5 4bfb3731049cd292346b5276f180b312).
- **Stress test:** 0 unsafe flips across 21 perturbations (stress_final3.txt).
- **Format fuzz:** all 8 shapes pass, 0 unsafe, equivalent shapes 819/819 identical (fuzz_final3.txt).

## Addendum, 21 Nov (after 14:49): Head of Financial Crime decisions
1. The 10 country-only clearances moved to REVIEW by the married-surname/dropped-middle-name guard **stay in REVIEW**.
2. **New rule:** same surname + an agreeing DOB (any DOB we trust, including a year-only list DOB) means a different first/given name alone cannot clear. The hit goes to REVIEW: "Escalated, not cleared: … Only the given names differ, which a middle name or a known-as name can explain. Reviewer: verify the customer's full legal name, including middle names, from the ID document." The independent verifier mirrors it. Fail-safe: if a DOB on either side cannot be read, it may be the one that agrees, so a shared surname with a different given name also goes to REVIEW (found by the stress test: two-digit years had let 2 of the 19 clear again). Late fix (15:40): DOBs on both sides that neither agree nor all contradict also go to REVIEW (3 more: H-088ACA, H-E82E48, H-F7F7BF), and web-search results are also matched up to known given-name variants when the exact query finds nothing (a transliterated listing keeps its search-derived DOB). Attacker test after this fix: 2 plausible unsafe (H-338C9D, H-38019A: list gives no DOB at all; closing them would move 19 more clearances and overturn policy 3). Transliterate stress perturbation: 0 unsafe. Final: 22 decisions moved vs b892317, all DIFFERENT_ENTITY → REVIEW; 398 / 123 / 298; 130 of 440 alerts fully cleared.

Effect against the previous output (b892317): **19 decisions changed**, all DIFFERENT_ENTITY (NAME_MISMATCH_VERIFIED) → REVIEW:
H-73AB0E, H-41D303, H-0C27A1, H-FE1415, H-F50B21, H-866A26, H-775DC0, H-849B4F, H-F3ED4F, H-32BBAE, H-F1E980, H-3BCC32, H-8B7A97, H-006E36, H-DDC553, H-CC0A18, H-380854, H-17B10F, H-A4722E.

- Totals: 401 DIFFERENT_ENTITY / 123 SAME_ENTITY / 295 REVIEW; 132 of 440 alerts fully cleared (was 420/123/276, 147).
- Labels: 48/50 (cost saved 94.4%, was 97.2%), 0 unsafe. The new miss is H-380854 (labelled DIFFERENT_ENTITY: verified SARAH JOHNSON vs listed "Johnson, James Jacob", years 1997/1998 agree), now REVIEW (cost 3).
- Every agreeing DOB among the 19 is year-only on the list side (often age-derived pairs such as 1986/1985), and several are a female verified name against a male listed name. A narrower reading (month-level agreement) would not have closed the attacker gaps, which are also year-only, so the rule is applied as stated.
- Attacker test: plausible unsafe clearances 5 → 3 (3,622 attacks on 418 non-cleared hits). The 3 left (H-338C9D, H-38019A, H-7D0FAB disguised as a dropped middle name) have no list DOB that agrees with the customer's, so the rule can't fire; closing them would mean never clearing on the surname rule.
- Stress test 0 unsafe; format fuzz all 8 shapes pass (six equivalent shapes 819/819 identical); 59 tests pass; runs byte-identical (final MD5 dc8f922d9607fb4fa93337c75984d254).
