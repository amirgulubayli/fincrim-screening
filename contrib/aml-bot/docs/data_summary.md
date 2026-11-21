# Track 2 (Sanctions screening: "Same Person or Not?"): data profile
Source: the workbook the user attached (read-only view of the dev set, built 2026-11-21 03:31). Profiled with pandas on 21 Nov 2026.
Raw outputs: profile_raw.txt (every column: null rate, distinct values, examples) and profile_deep.txt (links, formats, entity resolution stats).

## Why the file is 23 MB
There are no embedded images or objects; the drawing parts are empty. The size is just volume. Unzipped it is about 158 MB of XML:
Customers 48.8 MB, sharedStrings 28.5 MB, Accounts 26 MB, Onboarding 25 MB, KYC 15.8 MB, Notes 8.3 MB, Search 4.4 MB.
Hits text is long: profile_notes total about 763k characters (2 are cut at the 32,000-character Excel cap) and source_urls about 622k.
**The JSONL files in dev/ are the source of truth. Long texts are truncated in the workbook.**

## Tabs
| Tab | Rows | Key columns / notes |
|---|---|---|
| Alerts | 440 | alert_id, screened_at (2026-11-01 to 11-19), customer_name, entity_type (395 Individual / 45 Company), dob (57% filled), country (33%), city (17%), account_ref (39%), login_handle (16%), email (12%), hit_count, hit_ids |
| Hits | 819 | 702 Individual / 117 Company. match_score 90-100 (274 at 100). names, aliases (36%), dobs (**only 18.6%**), place_of_birth (7.6%), life_status (6 Deceased), deceased_date (4), nationality/citizenship (~13%), countries (58%), addresses (83%), identifiers, categories, primary_category, profile_notes (53%), sources, source_urls |
| Customers | 72,265 | record_id, entity_type (64,746 Ind / 7,519 Co), full_name (only 3,917 distinct, heavy namesakes, e.g. "Marta Lopez" ×101), preferred_name (22%), login_name (**100% null**), dob, nationality, residence_country, city, address, email (unique), phone, occupation, employer, registration_number (companies), onboarded_at, status (active 61,276 / closed 7,289 / dormant 3,700), last_kyc_refresh |
| Accounts | 86,659 | account_id (86,607 distinct, so **52 joint accounts** with more than one customer), customer_id, login_handle, phone, opened_at, status. 14,384 customers have more than one account |
| Onboarding | 72,265 | account_id, customer_id, kyc_applicant_id (1 per customer). **22,691 applicants have no KYC check** |
| KYC checks | 54,488 | provider (Sumsub, Veriff, Persona, Onfido, Companies registry lookup), verified_name, verified_dob, document_type/number/country, result (approved 49,574 / rejected 2,481 / expired 2,433), checked_at. 4,914 applicants have 2 checks |
| Notes | 31,895 | Keyed by requester_email (all match a customer). channel: kyc 22,575 (free-text "X verified NAME, DOB …, passport from …", which acts as a **KYC check written as a note**), phone 5,091, app 2,146, email 2,083. About 445 mention prior residence, e.g. "Residency and employment in Romania were confirmed … 2016 to 2020" |
| Search results | 16,882 | query = the name's lowercase words in alphabetical order. 1,758 queries, 2 to 54 results each (median 7). 75% of hit names have results. Synthetic snippets with age, month/year, role and country ("…Funmilayo Okafor, 29, as a politically exposed person" in India, Sept 1985) |
| Practice labels | 50 | 28 DIFFERENT_ENTITY / 12 SAME_ENTITY / 10 REVIEW. Codes: LOCATION_MISMATCH 15 (+2 combined), INSUFFICIENT_DATA 10, NAME_DOB(_LOCATION)_MATCH 8, DOB_MISMATCH 5 (+2), NAME_LOCATION_MATCH 4, NAME_MISMATCH_VERIFIED 3, TEMPORAL+LOCATION 1. 13 labels list more than one record for the same person, and 2 are AMBIGUOUS. Each has reference_reason and a path through the files |
| Policy | — | POLICY.md (identity SOP, 9 principles, reason codes) and name_variants.json (about 70 given-name groups plus company legal forms) |

**Count discrepancy:** the event web page says 379 alerts / 766 hits / 71,865 customers / 86,227 accounts / 53,977 KYC / 31,675 notes / 16,791 search results. The workbook and the Google Doc brief say about 440 / 819 (about 820) / 72,265 / 86,659 / 54,488 / 31,895 / 16,882. Check against the dev/ JSONL.

## Findings that shape the build
- **Customer linkage:** 170 alerts link by account_ref, 70 by login_handle, 54 by email, and **146 have no link**. Every link resolves (100%). All 146 unlinked alerts carry the screened dob and country, and 76 of them carry the city, so fuzzy search over Customers is needed. 58 alerts point at joint accounts, so the screened name and DOB have to pick the holder.
- **Same person, several records:** 43 phone+DOB groups have more than one record, and 164 phone numbers are shared by 348 records (family members alone do not count). Approved KYC document numbers are a second link.
- **Formats are messy:** customer DOBs are ISO 55%, dd/mm/yyyy 15%, "Month d, yyyy" 15% and "d Month yyyy" 15%. Countries mix ISO-2 codes and names (GB/United Kingdom), and nationality mixes demonyms and names (British/United Kingdom). Company alerts put the incorporation date in dob (e.g. Danske Bank A/S 1871-10-05).
- **Placeholders** are in screened names and in Customers: "RAHMAN TEST TEST", "SANI 0000 0000", "N/A GILBERT", "XXX MARTON XXX"…
- **Hit DOBs are sparse:** 152 hits have dobs (often ranges such as "1995; 1996"). 13 more have an age phrase in the first paragraph ("a 30-year-old" in Nov 1998, "reached the age of 38" in Jan 2025), and the wording varies, which makes an LLM extractor useful. 214 hits have dated-report lines ("According to …; Month d, yyyy:") that support the TEMPORAL_IMPOSSIBILITY test. The rest must rely on search results (filtered by listing type and country) or stay unknown.
- **Entity type:** 1 alert is a Company screened against an Individual hit (an ENTITY_TYPE_MISMATCH candidate).
- 6 hits are Deceased, and 4 of them have a date.
- Alert size: 344 alerts have 1 hit, and 8 have 15 hits.
