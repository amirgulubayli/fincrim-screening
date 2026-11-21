---
name: screening-cli
description: Run and interpret the screening engine CLI (run, explain, eval, repro, summary) and its --json output, and perform the Grok Bot morning health-check run. Use when checking decisions, scores, reproducibility, or reporting regressions.
---

# Screening CLI

All commands: `python system/screen.py <cmd>` (use `python3` on macOS/Linux). Default data folder `data/dev`. Add `--json` for machine output.

| Command | Use | Exit code |
|---|---|---|
| `run DATA OUT [--trace FILE] [--quiet]` | decide every hit; OUT = submission JSONL; trace adds tier/flags/rule trace | 0 |
| `explain HIT_ID [--data D]` | evidence trail for one hit | 2 if unknown hit |
| `eval [--data D] [--pred F] [-v]` | score vs `practice_labels.jsonl` | **1 if any unsafe clearance** |
| `repro [--data D] --runs 5 [--out-dir output/repro]` | fresh processes, different PYTHONHASHSEED | 1 if not byte-identical |
| `summary [--data D]` | dataset + decision counts | 0 |

The scored path is `system/run DATA OUT` (same as `run`).

## Reading output

- **run** JSON: `hits`, `decisions` (counts by decision), `tiers` (counts by HITL tier), `seconds`.
- **explain**: `HIT -> DECISION / REASON_CODE`, tier + flags, customer + evidence, reason, then one line per rule: `rule_id signal summary [WEAK: flag]`. A REVIEW with an empty trace usually means rules abstained (stubs) or no customer was found.
- **eval** JSON: decision score (cost saved vs all-REVIEW; costs: clear a true SAME/REVIEW = 60, true SAME -> REVIEW = 2, true DIFFERENT -> REVIEW/SAME = 3), reason/customer accuracy, `unsafe_clearances`. `-v` lists every wrong row; `<-- UNSAFE CLEARANCE` rows are top priority.
- **repro** JSON: `runs[]` (seed, sha256, seconds), `byte_identical`, `unstable_hits`, `decision_reproducibility`. Also written to `output/repro/report.json`.

Investigating a wrong hit: `eval -v` -> pick hit -> `explain HIT --json` -> find which rule spoke (or did not) -> fix in that rule's own issue.

## Morning run (Grok Bot)

Purpose: daily health check of `main`. **Never edit code, never commit, never push in a morning run.**

1. `git switch main && git pull --ff-only`
2. Run and save:
   ```
   python system/screen.py summary --data data/dev --json > out/summary.json
   python system/screen.py eval    --data data/dev --json > out/eval.json
   python system/screen.py repro   --data data/dev --runs 5 --json > out/repro.json
   ```
   (`out/` is gitignored.) Record exit codes; a crash is itself a finding.
3. Write a short markdown report:
   ```
   ## Morning run YYYY-MM-DD (main @ <short sha>)
   | Metric | Today | Previous |
   | hits / decisions / tiers | ... | ... |
   | decision score | ... | ... |
   | unsafe clearances | ... | ... |
   | byte-identical (5 runs) | ... | ... |
   | runtime (s) | ... | ... |
   Regressions: <list or "none">
   ```
   Previous = numbers from yesterday's morning-run issue, if any.
4. Open or update the issue titled **"Morning run YYYY-MM-DD"** with the report (`gh issue list --search "Morning run YYYY-MM-DD"`; `gh issue create` / `gh issue comment`).
5. For each regression file one issue:
   `python tools/task.py new "Regression: <what>" --area engine --priority p0`
   Regressions: unsafe clearances > 0, repro not identical, decision score drop, any command crashing, runtime > 10 min.
6. Stop. Do not claim or fix anything.
