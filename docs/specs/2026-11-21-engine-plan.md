# Engine plan: from walking skeleton to scored decisions (2026-11-21)

Status at 12:30: the walking skeleton runs end to end. `screen eval` on dev gives every
hit `REVIEW`, decision_score 0.0, 0 unsafe clearances. Only `rules/entity_type.py` is
real; the other rules return `[]`, `names_agree()` raises `NotImplementedError`, the
date parser misses slash dates, the country table covers 7 countries, and resolution
ignores unlinked alerts, joint accounts and the identity graph.

Code freeze is about 17:00. This document is the shared picture of the data and the
work split. `tools/seed_issues.json` turns section (b) into GitHub issues.

All counts come from throwaway scripts over `data/dev/*.jsonl`. The data and the SOP stay
out of the repo; the snippets below only show the *format*, with values changed.

---

## (a) Data observations that matter

Volumes: 440 alerts, 819 hits (442 distinct hit names, so the same list profile shows
up in many alerts), 72,265 customer records, 86,659 account rows, 54,488 KYC checks,
31,895 notes, 16,882 search results, and 50 practice labels (28 DIFFERENT, 12 SAME,
10 REVIEW).

### Dates

| Field | Shapes seen (count) |
|---|---|
| `customers.dob` | ISO `YYYY-MM-DD` 39,735 · `DD/MM/YYYY` 11,015 · `D Month YYYY` 10,849 · `Month D, YYYY` 10,606 · year only 28 · `YYYY-MM` 1 · `Month YYYY` 1 · null 30 |
| `kyc_checks.verified_dob` | the same mix: ISO 34,135 · slash 6,188 · `D Month YYYY` 6,161 · `Month D, YYYY` 6,127 · year 25 · `YYYY-MM` 2 · **null 1,850** |
| `kyc_checks.checked_at`, `notes.created_at`, `alerts.screened_at` | always ISO, so sorting them as text is safe |
| `alerts.dob` | ISO 204 · year only 34 · `D Month YYYY` 12 · `YYYY-MM` 2 · **null 188** |
| `hits.dobs` | `;`-separated list. Year 240 · ISO 50 · `YYYY-MM` 3. Only 152 of 819 hits have any |
| `hits.deceased_date` | `YYYY-MM` 2 · ISO 2 (6 hits are `life_status: Deceased`, 2 of them with no date) |

- **Slash dates are day-first.** In customers, KYC and notes, the second number is never
  above 12, but the first is above 12 in 6,348 customer, 3,561 KYC and 3,157 note values.
  Parse `NN/NN/YYYY` as DD/MM. 4,667 customer values have both numbers at 12 or below.
  These are still DD/MM, but a parser that guesses MM/DD would silently swap month and
  day and produce false `DOB_MISMATCH` clears.
- Profile-note DOB sentences use `born in YYYY` and `born on D Month YYYY`. Age sentences
  come in many wordings: "aged 30, in November 1998", "reached the age of N" in a month,
  "was N years old", "at age N", "The age of X was N when … in Month YYYY". A
  wording-agnostic extractor (an age number plus a `Month YYYY` in the same sentence of the
  first paragraph) generalises better than a list of templates.
- Search snippets carry an age and a month and year ("X, 27, … in a September 2014
  report"; "51 … Published in December 2007"; "27-year-old"). Each gives two possible
  birth years.

### Countries

- `customers.nationality` has 81 distinct values: names and demonyms mixed (Nigeria 7,242
  / Nigerian 3,023; British 2,307; Bangladeshi 2,265; Irish 769; Polish 664 …).
- `customers.residence_country` has 90 distinct values: names and ISO-2 codes mixed (US
  2,797, NG 2,778, GB 2,696, ES, DE, FR, BD, IT, IE, CA, AU …).
- `kyc_checks.document_country` is always a full name. `alerts.country` is null on 294 of
  440 alerts.
- Hit addresses carry a bracketed role tag (`United States [Associated]`, `[Business]`,
  `[PEP]`, `[Headquarters]`, `[Residency]`, `[Residential]`). Strip the brackets before
  taking the last comma part. `facts.py` does this.
- The long tail matters for location clears: Moldova, Slovenia, Panama, Peru, Ghana,
  Cote D'Ivoire, Haiti, Ecuador, Serbia, Vietnam, Syria, UAE, Russia and others. An
  unknown value must come out as a stable canonical string, never `None`. Otherwise a
  customer country would silently drop out of the set, and that is how false
  `LOCATION_MISMATCH` clears happen.
- KYC notes give the document country as a demonym too ("using a Nigerian national ID").

### How notes state verified identity (SOP P4)

- 22,575 of 31,895 notes are `channel: kyc`. All of them describe a successful check:
  zero mention rejected, expired or failed results (rejected and expired results appear
  only in `kyc_checks`, 2,481 + 2,433). The `phone`, `app` and `email` notes are support
  chatter, plus **residence statements**.
- KYC notes come in dozens of templates. The provider is one of Persona, Onfido, Veriff
  or Sumsub. The name is in UPPER CASE (`NILS MARTIN`). The DOB uses any of the four date
  formats. The document country comes in brackets, after `from`, after `issued in`,
  after `ctry`, or as a demonym. Examples of the shapes:
  `ID check complete: <Provider> processed <doc> (<Country>) for <NAME>, DOB <date>, on <iso>.`,
  `<doc> from <Country> verified by <Provider> on <iso> for <NAME>, DOB <date>.`,
  `<NAME>'s identity was confirmed via <Provider> on <iso> using a <Demonym> <doc>. DOB recorded as <date>.`
  So extract by anchors (DOB or born followed by a date; a run of uppercase words for the
  name; a country lexicon), not by a fixed list of templates. The check date is the ISO
  date in the text, or else `created_at`.
- Notes link to customers **only by `requester_email` = `customers.email`**. They are
  never keyed by record id.
- About 2,300 non-KYC notes are residence or employment statements: "The client
  maintained a job and home in Portugal between 2010 and 2011", "Records show the client
  lived and was employed in X", "Employment and residency in X from YYYY to YYYY". P6
  counts these countries for the customer ("places the notes say they lived"). Leaving
  them out makes the customer's country set too small, which is unsafe for location
  clears.

### Hit profile notes (SOP P5)

- 431 of 819 hits have `profile_notes`. 214 of them contain `According to …` lines.
- `According to` lines: 639 have the exact form `According to <source>; <Month D, YYYY>: …`.
  23 use `; <Month YYYY>:` with no day, and 143 are other shapes (for example the source
  name runs on with no `; date:`). **Only the first form is a dated report.** The other
  166 must be ignored. They are a deliberate trap.
- Profile notes also contain career date ranges ("(December 10, 2011 - May 02, 2018)"),
  sanctions "as of 2022/03/31" lines, and Spanish or French duplicate sections
  (`Trayectoria:`, `Partido Político:`). None of these are dated reports or DOB sources.
- DOB and age sentences only count in the **first paragraph** (text before the first
  blank line).
- Some text has non-ASCII accents (`Paweł`, `Thuận Thị Lê`). Read the files as UTF-8 and
  compare names without accents.

### Search results (SOP P5)

- They are filed under the hit name's lowercase parts, sorted alphabetically (`"bhuiyan
  shakil"`). Titles end in `- report`, `- directory`, `- news` or `- profile`, about 4.2k
  each.
- Each of the six listing phrases appears in about 1.1k snippets. **Most are namesakes.**
  A result counts only if its phrase equals the hit's phrase, taken from
  `primary_category`, or else the first entry of `categories`. `primary_category` is null
  on 345 hits. Some categories are not in the map, such as `Registration` (23) and
  `Other Segment`, and these fall back to "named on a watchlist". The result must also
  name a country tied to the hit, in any phrasing: "in Pakistan", "within Cote D'Ivoire",
  "a Netherlands resident", "Brazilian authorities", "by a Cote D'Ivoire news
  organization". That needs demonyms too.
- Search results are only a DOB source when the hit has no `dobs` and no profile-note
  DOB. In the labels, 5 of 50 paths use one (`SR-…`, "same role and country as the hit").

### Alert links and resolution (SOP P2, P3)

| Link on alert | Alerts | Notes |
|---|---|---|
| `account_ref` only | 170 | **58 point to joint accounts (2 holders)**, 112 to a single holder |
| `login_handle` only | 70 | always exactly 1 customer |
| `email` only | 54 | always exactly 1 customer |
| none | **146** | must search by screened name, DOB, country and city |

- No alert has more than one link type.
- Only 52 accounts in the whole bank are joint, and 58 alerts point at one. The data
  plants these on purpose. Pick the holder the screened name and DOB fit, and never use
  the other holder's facts.
- `customers.login_name` is never filled (0 rows). `preferred_name` is filled on 16,216.
- Label paths (50): 12 unlinked searches, 8 joint-account picks, 13 identity-graph merges
  ("same phone and date of birth, or same document number"), 38 use an approved KYC
  check, 21 use a note, and 2 have "2 different people fit" (customer_record_id
  `AMBIGUOUS`).
- 164 phone numbers are shared by several customer records. The SOP warns that a shared
  phone alone is not a link (family members); the full DOB must match as well.

### Screened names and entity types

- Screened names include placeholders: `UNKNOWN NADEEM`, `BROOKS N/A`, `AZAM NA NA`,
  `RAHMAN TEST TEST`, `XXX MARTON XXX`, `BUSTAMANTE 0000`. An individual with fewer than
  two real name parts can be neither confirmed nor cleared on name.
- Alerts: 395 individuals and 45 companies. Hits: 702 individuals and 117 companies. Only
  1 hit differs in type from its alert, so `ENTITY_TYPE_MISMATCH` (already implemented) is
  worth very little.
- No company hit has `dobs`, so there is no incorporation-date path in dev. The rule is
  still needed for the unseen set.
- 188 alerts have no screened DOB. For those, resolution must lean on name, country and
  city, and DOB facts come only from the resolved records.

### Practice-label economics (dev, 50 hits)

All-REVIEW costs 28×3 + 12×2 = **108**. Every correct DIFFERENT saves 3, every correct
SAME saves 2, and a single unsafe clear costs 60, which wipes out 20 correct clears.

Reason mix of the DIFFERENT labels, counting each accepted code: LOCATION_MISMATCH 18,
DOB_MISMATCH 7, NAME_MISMATCH_VERIFIED 7, TEMPORAL_IMPOSSIBILITY 1. The 12 SAME labels
are 8 NAME_DOB(_LOCATION)_MATCH and 4 company NAME_LOCATION_MATCH. All 10 REVIEW labels
are "name and country agree, DOB not comparable". **These are exactly the cases where an
over-eager name or location rule would wrongly confirm.**

---

## (b) Work breakdown and dependencies

Keys match `tools/seed_issues.json`.

```
date-parser ─┐            country-norm ─┐          name-matcher ─┐
             ├─> resolve-unlinked ──────┤                        │
             │   identity-graph         │                        │
kyc-notes-identity (needs date-parser for parsing, not to start) │
hit-facts-notes-sr ─────────────────────┤                        │
rule-dob ◄─ written against parse_date ─┘  rule-location ◄──┘    rule-name ◄──┘
rule-deceased-temporal          evidence-precision (after the rules land)
readme-docs · dashboard-hitl · demo-script · fatf-context-panel (independent)
```

Rules are written against the existing interfaces (`parse_date`, `PartialDate`,
`normalize_country`, `names_agree`, `HitFacts`, `Person`). Each rule ships with unit
tests that use hand-built `Context` objects, so it can merge before its normaliser is
finished. On real data, a rule simply abstains until the normaliser lands.

### Suggested 3-person split (plus a floater for the dashboard and demo)

| Owner | Issues (in order) | Primary files |
|---|---|---|
| **engine-A: normalisation and names** | date-parser → country-norm → name-matcher → rule-name | `normalize/dates.py`, `normalize/countries.py`, `normalize/names.py`, `rules/name.py` |
| **engine-B: resolution and identity graph** | resolve-unlinked → identity-graph → kyc-notes-identity | `resolve.py`, new `identity.py`, new `normalize/notes.py` |
| **engine-C: hit facts and rules** | hit-facts-notes-sr → rule-dob → rule-location → rule-deceased-temporal → evidence-precision | `facts.py`, `rules/dob.py`, `rules/location.py`, `rules/temporal.py` + `rules/deceased.py`, `explain.py` |
| **floater: dashboard, demo and docs** | readme-docs, dashboard-hitl, demo-script, (p2) fatf-context-panel | `system/README.md`, `dashboard/*`, `demo/*` |

Integration rhythm: merge to main every time `screen eval` stays at 0 unsafe clears.
Re-run eval after each merge and post the score in the team channel. Rule PRs that
raise unsafe clears are reverted, not patched forward.

---

## (c) Expected score gain per item (dev practice labels)

Savings use the cost table: DIFFERENT→correct is +3, SAME→correct is +2. The unseen set
(~550 hits) should have a similar mix, so roughly ×11.

| Item | Unlocks (dev labels) | Est. dev gain | Why |
|---|---|---|---|
| country-norm + rule-location | 15 pure LOCATION_MISMATCH + 3 multi-code (e.g. Netherlands vs United States; Spain vs Brazil; customer has Ghana + Netherlands vs hit United States) | **+45 to +54** | the biggest bucket; needs notes residence countries and KYC doc country in the customer set |
| name-matcher + rule-name | 7 NAME_MISMATCH_VERIFIED (verified "CHINEDU AZEEZ" vs hit "Ridwan, Azeez" — one match is not enough; "Jonathan Reyes Whitfield" vs "John Whitfield") and the name half of all 12 SAME | +9 alone, enables +24 | without agreement there is no SAME at all |
| date-parser + rule-dob | 7 DOB_MISMATCH and the DOB half of 8 person SAMEs | +12 to +21 | day-first slash dates; "1994, 1995" style hit year pairs |
| hit-facts-notes-sr | DOB from profile notes (3 labels: "profile notes put its birth year at 1976 or 1977") and from search results (5 labels), the dated report for 1 TEMPORAL label | +10 to +15 | |
| kyc-notes-identity | 21 of 50 paths cite a NOTE as the DOB or verified-name source | multiplier on DOB and name rules (~+20) | without it, those hits fall back to declared DOB/name: weaker, sometimes wrong |
| resolve-unlinked | 12 labels resolved by name, DOB, country and city | +20 to +30 | today these get `customer_record_id: null` and no facts |
| identity-graph | 13 labels merge 2 records (extra countries for location, verified name for NAME_MISMATCH) | +10 to +20, and safety | missing merged countries leads to false LOCATION clears |
| joint-account filtering (inside resolve-unlinked) | 8 labels | +10 to +16 | today both holders count, so different decisions come out as AMBIGUOUS |
| rule-deceased-temporal | 1 dev label, plus 4 dated deceased hits | +3 (more on unseen) | |
| evidence-precision | reason score (25% of the unseen weight) on all 40 decided labels | reason points, not cost | cite only the decisive ids, like the label `evidence` |

Realistic dev target by 17:00: 80 to 95 of the 108 cost saved, with 0 unsafe clearances.

---

## (d) Unsafe-clearance risk list and guards

Clearing a true SAME or REVIEW costs 60. Every contradiction rule must abstain whenever
a needed fact is missing or uncertain.

| # | Risk | Guard (must be in the rule and its tests) |
|---|---|---|
| R1 | DD/MM read as MM/DD swaps month and day, giving a false DOB_MISMATCH | slash dates are day-first. If a value is unparseable, abstain. Never use an `alerts.dob` that contradicts the resolved verified DOB |
| R2 | Year-only DOB treated as Jan 1 | `PartialDate` keeps unknown parts unknown. Contradict on month or day only when both sides give them **in the same year** |
| R3 | Age-at-date gives two years, and only one was checked | always expand to {y-1, y}. Clear only if the customer year is more than 1 outside **every** candidate year, across **every** hit DOB |
| R4 | Search result about a namesake used as the hit's DOB | require the exact listing phrase **and** a country tied to the hit. Search results only when there is no `dobs` and no notes DOB. Mark `weak=True` so the decision goes to BORDERLINE |
| R5 | Customer country set too small (merged records, notes residence, KYC doc country or demonym missed; unknown country normalised to None) | location clears only when the customer set is built from all records of the person, the latest approved KYC doc country and notes residence statements. Every value is normalised and none dropped. Abstain if the customer set or the hit set is empty |
| R6 | Location clear when name and DOB agree | the compiler already turns this into REVIEW. Keep that test |
| R7 | "International", "Europe" or a bracket tag read as a country | NOT_COUNTRIES plus bracket stripping. Add tests |
| R8 | NAME_MISMATCH from the record name or a nickname instead of the **verified** name | the name contradiction may only use the latest **approved** KYC or notes verified name. With no verified name, abstain. Check every hit name and alias |
| R9 | Placeholder names (NA, TEST, XXX, digits) | fewer than 2 real parts means no name verdict either way |
| R10 | Wrong customer: the other joint holder, a namesake in an unlinked search, a shared phone without a DOB match | candidate filtering by screened name **and** DOB parts. Graph merge needs phone **and** full DOB, or a document number on approved checks. If more than one person is left, decide per person and fall back to AMBIGUOUS REVIEW |
| R11 | Rejected or expired KYC used | filter `result == approved` (already done); sort by `checked_at`. KYC notes in dev are all successes, but any note saying rejected, failed or expired in the unseen set must be skipped |
| R12 | Non-dated "According to" lines (month-year, Spanish copies, career ranges) used for TEMPORAL | exact regex `^According to [^;\n]+; <Month> <D>, <YYYY>:` per line. Earliest such date only. Customer age under 16 from the **verified** DOB, using the conservative edge (latest possible birth date) |
| R13 | DECEASED from `life_status: Deceased` with no date, or a `YYYY-MM` date compared too precisely | need a parsed death date strictly before `screened_at`, comparing only the parts both give. If the date is missing, abstain |
| R14 | Company vs person heuristics | only `entity_type`. Company DOB means incorporation and comes only from `dobs` |
| R15 | Nondeterminism | sort every collection by id. No set iteration order in output. `screen repro --runs 5` must be byte-identical (run locally before every PR; `task.py done` checks) |
