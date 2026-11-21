---
name: morning-run
description: Daily screening health check (the "Grok Bot morning run"). Runs the deterministic eval + 5-run repro + review-queue summary, writes out/morning/YYYY-MM-DD.md, opens or updates the GitHub issue "Morning run YYYY-MM-DD" and files one issue per regression. Use when asked for the morning run, a health check, /screen-morning, or "is main still safe?". Never edits code.
argument-hint: "[data folder, default data/dev]"
---

# Morning run

You are the reporter, not the decider. The screening engine's numbers are final. Your job is to run it,
put the numbers in front of people, and file issues for regressions. You never change a decision, a rule,
a test or any file under `system/`, `app/`, `tools/` or `tests/`.

## Hard rules

- **Never edit, commit or push anything.** You do not need a file-editing tool at all: the report is
  written by `tools/morning_run.py` and you post it as-is. Do not run `git commit`, `git push`,
  `git stash`, `task.py claim` or `task.py done`.
- **Report numbers exactly as the tools print them.** Do not round differently, re-score or
  "correct" anything. If a command fails, the failure is the finding: quote its exit code and last lines.
- **Never paste data rows.** The issue goes to a public repo. Hit IDs, counts and scores only; no names,
  dates of birth or other customer fields from `data/`.

## Steps

1. **Run the deterministic part** (it writes the report and prints a JSON summary):

   ```
   python tools/morning_run.py --data <folder>
   ```

   `<folder>` is the argument the user gave (`$ARGUMENTS`), or `data/dev` if none. Use `python3` on macOS/Linux. If today's `out/morning/YYYY-MM-DD.json` already exists because the
   scheduler (`tools/morning_run.ps1`) ran it this morning, you may read that instead of re-running.
   Exit code 1 means a regression was found; that is expected output, not a reason to stop.

2. **Cross-check with the MCP tools** if the `fincrim-screening` server is connected (tool names may be
   prefixed `fincrim-screening__`): `screen_eval` (score, `unsafe_clearances`, `unsafe_hits`),
   `screen_run` (counts by decision and tier) and `review_queue` with `tier: HUMAN_REVIEW` and
   `tier: BORDERLINE`. If the MCP numbers and the report disagree, say so in the issue; do not pick one.

3. **Open or update the issue** titled exactly `Morning run YYYY-MM-DD` (today's date), posting the
   deterministic report unchanged:

   ```
   gh issue list --state all --search "\"Morning run YYYY-MM-DD\" in:title" --json number,title
   gh issue create --title "Morning run YYYY-MM-DD" --body-file out/morning/YYYY-MM-DD.md   # if none
   gh issue comment <number> --body-file out/morning/YYYY-MM-DD.md                         # if it exists
   ```

4. **Add your summary as a separate comment**, so the engine's report and your narrative never mix:

   ```
   gh issue comment <number> --body "**Grok summary (narrative only; numbers above are the engine's):** <2-3 sentences>"
   ```

   Cover: is it safe (unsafe clearances), is it reproducible (byte-identical), and what changed since
   the previous run (the "Previous" column). Use only numbers that appear in the report.

5. **File one issue per regression** listed in the JSON `regressions` array (skip any whose title already
   exists as an open issue; check with `gh issue list --search "<title> in:title"`):

   ```
   python tools/task.py new "Regression: <what> (morning run YYYY-MM-DD)" --area engine --priority p0 --body "<numbers + link to the morning-run issue>"
   ```

   Regressions are: unsafe clearances > 0, repro not byte-identical, decision score lower than the
   previous run, any engine command crashing, runtime over 10 minutes.

6. **Stop.** Reply with the issue URL, the three headline numbers (decision score, unsafe clearances,
   byte-identical yes/no) and the regression issue URLs. Do not claim or fix anything.
