# Demo script (3 minutes)

**Run:** `system/demo dev` (in the team repo: `contrib/aml-bot/engine/demo data/dev`). It prints three real development-set cases with plain-English reasons in about 12–15 seconds. The engine (`system/run`) contains no case IDs; the demo only chooses which three cases to show.

## 0. Set-up line (15 s)
"Every screening alert is a question: is this listed person our customer? Clearing a real match is the worst mistake, so our system clears a hit only when it can prove the hit is someone else, and it double-checks every clearance."

## 1. A clear mismatch, cleared (45 s): hit H-58A39C
- **Same name:** Timothy Pangelinan Villagomez, a PEP.
- **What the system found:** the bank's verified KYC check (KYC-232AE0) gives a birth date of 28 March 1965; the listing says 10 September 1962.
- **Outcome:** CLEARED, DOB_MISMATCH. It cites only the record that decided it.
- **Say:** "The second check re-derived this from the raw records before letting it through."

## 2. A real match, sent to compliance (45 s): hit H-4D76E3
- **Same name:** Mohammad Nadeem Ghani, on a sanctions list.
- **The trap:** the customer record says 1963, which would "clear" against the listing's 1968.
- **What the system found:** a newer verified KYC note (NOTE-4E8A14) says 17 February 1968. Verified beats declared, so the dates agree; both are also linked to Pakistan and the UK.
- **Outcome:** MATCH, NAME_DOB_LOCATION_MATCH, sent to the compliance team.
- **Say:** "A naive system clears this sanctioned person. Ours doesn't."

## 3. Not enough to decide, escalated with guidance (45 s): hit H-B1018D
- **The case:** customer Dejan Jovanovic is linked to Serbia; the PEP listing says Montenegro.
- **Why it isn't cleared:** the two countries were one state, so older listing data may name either.
- **Outcome:** ESCALATED. The reason tells the analyst exactly what to check: the dates of the listing's country information, place of birth and nationality.
- **Say:** "This is Ozwald's policy call, as Head of Financial Crime, encoded as a rule."

## 4. Close (30 s)
- **Labels:** 48/50, with 0 unsafe clearances.
- **Workload:** 30% of alerts (130 of 440) fully cleared automatically.
- **Robustness:** 0 unsafe flips across 21 data perturbations and 8 file formats.
- **Runs:** identical every time; about 20 seconds; £0.

"It clears what it can prove, explains every decision and escalates the rest, with instructions."
