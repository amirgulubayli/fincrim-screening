# Is my customer sanctioned? A screening adjudicator that only clears what it can prove

**Track 2 · Grok Bot London Financial Crime Hackathon · built with Ozwald, Head of Financial Crime at a UK bank**

## The problem
Every name-screening alert has to be looked at by an analyst, and almost all of them are namesakes. Clearing a real match is the worst outcome: a sanctioned person stays a customer. A system that sends everything to review is safe but useless. The customer's identity is also spread across five systems that don't share a key, and the listed person's date of birth is often buried in free text or web results.

## Our approach
- **Contradictions first.** A hit is cleared only when a hard fact rules it out:
  - a different date of birth, against every date the bank holds;
  - countries with no overlap;
  - a verified legal name that doesn't match;
  - person versus company;
  - death before screening;
  - too young at the first report.

  A hit is confirmed only when name, date of birth and country agree. Everything else goes to a person.
- **The bank's identity SOP, encoded as written.** Verified identity beats what the customer typed in. Records sharing a phone and a full date of birth count as one person. If several people fit the alert and they lead to different decisions, the hit is escalated.
- **Head of Financial Crime calls built in.** For example:
  - Countries that were once one state (former Yugoslavia, former USSR, Sudan/South Sudan) don't clear.
  - A business address alone doesn't clear.
  - Patronymic surnames (Umar, Ibrahim, Alhassan…) and a surname-only overlap are escalated, with guidance for the analyst.
- **Fail-safe by design.** If a date, country or field can't be read for certain, it counts as unknown and is never used to clear. If a whole table or key field can't be understood, the system clears nothing.
- **Attacker test.** We tried to sneak 355 plausible true matches past the system with 3,649 disguises: transliterations, swapped or dropped names, married surnames, US date order, typos, former-state countries. The fixes cut the clearances from 24 to 2; the 2 left are listings with no date of birth at all, the cost of keeping the surname rule.
- **Explain or escalate.** Every clearance must be stated in one sentence with the field, our value, the list's value and a trusted source (e.g. "Cleared on date of birth: verified KYC check KYC-DE8245 gives 1995-12-17, the list gives 1978 or 1979"). If it can't be stated that way, it goes to review.
- **Common-name context.** Reviews note when a listed name matched several other customers ("likely a common-name hit: prioritise a DOB or ID check"). This context is never used to clear.
- **Independent second check.** Every clearance is re-derived from the raw records by a separate verifier. If the verifier can't reproduce the clearance, the hit goes to review.

## Results (development set: 440 alerts, 819 hits, 72,265 customer records)
- **Practice labels: 48 of 50 correct, 0 unsafe clearances.** Both misses are different people sent to review, which is the safe direction; one comes from our own rule that a shared surname plus a matching birth year never clears on a different first name alone.
- **Workload: 130 of 440 alerts (30%) fully cleared automatically,** with 398 of 819 hits cleared and 123 confirmed matches sent to compliance.
- **Stress test:** the same data was rewritten 21 ways (date formats, name order, accents, spellings, country codes, junk lines…). Result: **0 unsafe flips.**
- **Format-fuzz test:** the official file format isn't published, so the data was fed in 8 different shapes, plus the raw spreadsheet:
  - CSV, JSON arrays, hits nested inside alerts, one flat row per hit, and camelCase or renamed fields;
  - odd date formats with optional fields missing;
  - a key field missing, and unknown check results.

  Results:
  - **No crashes and 0 unsafe clearances** in every shape.
  - Where the data was the same, the decisions were **identical (819/819)**.
  - Where data was missing, hits only moved to review.
- **Deterministic and cheap.** Five runs give byte-identical output. A run takes about 20 seconds, makes no AI calls and costs £0.

## Why a regulator can live with it
- **Every decision explains itself.** Each one comes with a 2–4 sentence reason that names this hit's own facts and cites the record IDs it rests on, which gives a ready-made audit trail (MLRs 2017, FCA SYSC 6.3, OFSI expectations).
- **Escalations say what to check.** Every review starts "Escalated, not cleared:" and tells the analyst what to do, e.g. "check the dates of the listing's country information".
- **No black box.** The rules are readable, no machine-learning model makes a decision, and changing a policy is a one-line, testable change. There are 59 automated tests.
- **Errors lean the safe way.** Uncertainty costs analyst time, never a missed sanctions match.

## Limits (honest)
- **Tuned on 50 labels.** Expect roughly 60–85% of the possible saving on unseen data, not 94%. New wording of ages and notes falls back to review, which is safe but costs points.
- **Unknown file layout.** If the official files use field names we have not anticipated, the system detects it and clears nothing. That is safe, but resolves nothing until mapped.
- **Not a sanctions-policy engine.** It decides identity only; risk appetite and the decision to exit a customer stay with the bank.
