# 3-minute demo script (compliance judges)

**Story in one line:** every screening hit gets a decision a regulator can audit: two independent engines, same answer
every run, every decision traceable to an SOP clause and record IDs, the machine clears only what the SOP and two
checks allow, and Grok explains but never decides.

All hit IDs below are real dev hits, picked from
`python system/screen.py run data/dev out/o.jsonl --trace out/t.jsonl` and checked with `screen explain`.

## Numbers to quote (dev)

| | |
|---|---|
| Decision score | 0.9722 (49 / 50 practice labels) |
| Unsafe clearances | 0 |
| Evidence precision | 1.0 |
| Tiers | 397 AUTO_CLEAR / 98 AUTO_ESCALATE / 58 BORDERLINE / 266 HUMAN_REVIEW |
| Run | ~75 s, £0, no model |
| Repro | 5 runs byte-identical |

## Setup (before the judges arrive)

| What | Command / action |
|---|---|
| Dashboard | `$env:SCREEN_DATA="C:/path/to/data/dev"; uv run uvicorn app.server.main:app --port 8000` (built UI); wait for the engine to load (~75 s), open <http://localhost:8000> |
| Repro report | run `python system/screen.py repro --data data/dev --runs 5` **beforehand** (5 x ~75 s); the dashboard badge reads `output/repro/report.json` |
| Fresh morning report | `powershell -File tools/morning_run.ps1` - issue "Morning run YYYY-MM-DD" open in a browser tab |
| Grok | `grok` TUI open in the repo, plugin trusted (`grok inspect` shows `fincrim-screening` enabled); warm it with one question so the engine run is cached |
| Fallback | screenshots of every step in `docs/demo/` (take them during the dry run) |
| Font size | terminal + browser zoomed so the back row can read a hit ID |

A fresh `screen explain` process re-runs both engines (~75 s), so during the demo open hits in the dashboard (already
loaded) rather than running `explain` live.

## Script

| Time | Show | Say |
|---|---|---|
| 0:00-0:20 | Dashboard home | "A bank screens customers against sanctions and PEP lists. Most hits are false positives, but clearing a real one is the expensive mistake: the brief prices it at 60 times a needless review. Our system clears only what the SOP allows, twice." |
| 0:20-0:40 | Header KPIs / morning-run issue | "Decision score 0.97, 49 of 50 practice labels, **0 unsafe clearances**, evidence precision 1.0, five runs byte-identical. No model, no API cost." |
| 0:40-1:00 | Architecture slide (mermaid from README) | "The decision core is our teammate's AML Bot engine, with policy calls from a Head of Financial Crime and its own second check. Our modular rule engine re-decides every hit independently. If the core would clear but our check sees a match, it goes to review. Any other disagreement goes to a second analyst." |
| 1:00-1:20 | Queue -> AUTO_CLEAR **H-58A39C** | "Cleared automatically. The customer's approved KYC check KYC-232AE0 gives a date of birth two and a half years after the listed person's only DOB, and nothing the bank holds agrees. The core's second check reproduced it, and our engine found the same DOB contradiction independently. Evidence: one record." |
| 1:20-1:40 | AUTO_ESCALATE **H-4D76E3** | "Two customer records merged into one person by phone and DOB. The record DOB is off, but the latest approved check, NOTE-4E8A14, matches the hit, as do name and both countries. Both engines confirm, so it goes straight to compliance. This hit was once wrongly cleared on the record DOB; that is why a clearance must now hold against every DOB the bank holds." |
| 1:40-2:05 | BORDERLINE **H-AF6CD2** (flag `ENGINES_DISAGREE`) | "Two customers fit this alert and the core clears both on location. Our engine would not: their birth years are within a year of the listed person's, and a country difference alone must not clear a hit whose DOB may agree. The decision stands, but a second, different analyst must confirm it. Automation where both engines agree, four eyes where they do not." |
| 2:05-2:25 | HUMAN_REVIEW **H-B1018D** (flag `ESCALATED_BY_POLICY`) | "Literally a location mismatch, Serbia against Montenegro. But they were one state, so listing data may name either. The Head of Financial Crime's call: escalate, not clear, and tell the reviewer what to check: the dates of the listing's country data, place of birth, nationality." |
| 2:25-2:40 | Case file -> reviewer action | "The analyst accepts or overrides with a name and a note; BORDERLINE needs a second analyst. Every action lands in the audit trail, exportable as CSV. The engine output is never edited." |
| 2:40-2:55 | Grok TUI: *"why was H-58A39C cleared?"* | "Grok, our sponsor's agent, calls the plugin's read-only `screen_explain` tool and turns the trace into plain English. Every fact it quotes is from the trace; it has no tool that can change a decision." |
| 2:55-3:00 | Back to the KPIs | "Two deterministic engines, dual control, human-in-the-loop tiers, and an LLM that explains but never decides." |

## Showcase hits

| Slot | Hit ID | Decision / reason code | Flags | One-line story |
|---|---|---|---|---|
| AUTO_CLEAR | `H-58A39C` | DIFFERENT_ENTITY / DOB_MISMATCH | - | approved KYC DOB (KYC-232AE0) contradicts the hit's only DOB; both engines agree; practice label DIFFERENT |
| AUTO_ESCALATE | `H-4D76E3` | SAME_ENTITY / NAME_DOB_LOCATION_MATCH | - | identity merge (CUS-970916 + CUS-906311); latest approved check NOTE-4E8A14 matches; practice label SAME |
| BORDERLINE (engines disagree) | `H-AF6CD2` | DIFFERENT_ENTITY / LOCATION_MISMATCH, customer AMBIGUOUS | `ENGINES_DISAGREE` | core clears two candidate customers on location; check holds back because birth years are close |
| BORDERLINE (engines disagree, alt.) | `H-2C6B2E` | SAME_ENTITY / NAME_DOB_LOCATION_MATCH | `ENGINES_DISAGREE` | core confirms on a birth year from a search result (SR-1054CF); check reads a full DOB in the hit's profile that differs from the customer's (NOTE-BCDE4B); escalation stands, 4-eyes |
| BORDERLINE (weak fact) | `H-622EDD` | DIFFERENT_ENTITY / DOB_MISMATCH | `DOB_FROM_SEARCH_RESULT` | the hit's DOB exists only in a web search result (SR-A88751); practice label DIFFERENT |
| HUMAN_REVIEW (policy) | `H-B1018D` | REVIEW / INSUFFICIENT_DATA | `ESCALATED_BY_POLICY`, `ENGINES_DISAGREE` | Serbia vs Montenegro, former single state: escalated with reviewer guidance |
| HUMAN_REVIEW (plain) | `H-A22C42` | REVIEW / INSUFFICIENT_DATA | - | name and country agree but the hit has no DOB; practice label REVIEW |
| ENGINES_CONFLICT | none on dev | - | - | core-clears / check-confirms never happened on dev; the guard is for unseen data |

## Exact commands

```
# dashboard terminal (only `screen ...` commands are accepted there)
screen eval --data data/dev
screen explain H-58A39C --data data/dev

# same from a shell
python system/screen.py eval --data data/dev -v
python system/screen.py explain H-AF6CD2 --data data/dev
python system/screen.py repro --data data/dev --runs 5      # ~6-7 min: run before the demo

# Grok
grok                                  # then: why was H-58A39C cleared?   or   /screen-explain H-58A39C
/screen-queue BORDERLINE 5
```

## Picking other showcase hits

```
python system/screen.py run data/dev out/demo.jsonl --trace out/demo_trace.jsonl
python -c "import json; [print(r['tier'], r['hit_id'], r['reason_code'], r['flags']) for r in map(json.loads, open('out/demo_trace.jsonl', encoding='utf-8'))]" | sort | head -80
python system/screen.py explain <HIT> --data data/dev        # is it clear in 15 seconds?
```

Choose hits with short `explain` output, evidence IDs and no `ENGINE_ERROR` flag. Never paste raw records into slides
or docs: cite IDs and paraphrase.

## If something breaks

| Failure | Recovery |
|---|---|
| Dashboard down | `python system/screen.py explain <HIT> --data data/dev` in a plain terminal (allow ~75 s) or the screenshots |
| Grok not signed in / no network | show the `explain` output and the morning issue's Grok comment from the dry run; "the plugin is offline, the engine is not" |
| repro too slow live | never run it live; show `output/repro/report.json` / the dashboard badge from the pre-run |
| Anything else | fallback screenshots in `docs/demo/` |
