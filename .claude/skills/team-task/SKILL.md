---
name: team-task
description: Pick up, build, and finish a GitHub issue in this repo using tools/task.py (list, claim, test, eval, note, done). Use whenever starting or handing off work on fincrim-screening.
---

# Team task loop

Follow AGENTS.md. Replace `claude` with your agent name (`grok`, `cursor`, `codex`, `human`).

1. **Check state.** `python tools/task.py status`. If you already hold a claim, continue it (read its comments first) instead of claiming another.
2. **Pick.** `python tools/task.py list` (optionally `--area rules`). Choose the lowest-numbered `p0`, then `p1`, then `p2`. Skip anything blocked or claimed.
3. **Claim.** `python tools/task.py claim N --agent claude`. This checks out branch `N-slug`. If the claim reports a race (someone else claimed first), `release N` and pick again.
4. **Read.** Issue body + comments, the files it names, `contracts.py`, and for rules `rules/entity_type.py` and the SOP clause (`data/dev/POLICY.md`).
5. **Baseline.** `python system/screen.py eval --data data/dev --json > /tmp/before.json` (keep numbers for the PR).
6. **Build with tests.** Write tests first in `tests/` using tiny synthetic records. Implement only what the issue asks. Stdlib only in `system/`. Return `[]` / REVIEW when unsure.
7. **Verify.**
   - `uv run pytest -q` passes
   - `python system/screen.py eval --data data/dev -v` exits 0 and shows `unsafe_clearances: 0`
   - `python system/screen.py repro --data data/dev --runs 5` shows `byte_identical: True`
   - `git diff --stat` has no `data/`, `.xlsx`, `.env`, or unrelated files
8. **Review (rules).** Run the `sop-reviewer` subagent on the diff; fix anything it flags.
9. **Learnings (optional).** New file `docs/learnings/<YYYY-MM-DD>-<slug>.md` for anything the next agent should know.
10. **Note.** `python tools/task.py note "Done: ... Eval before->after: ... unsafe 0. Left: ..."`
11. **Sync + finish.** `python tools/task.py sync`, re-run tests, then `python tools/task.py done` (or `--draft` if incomplete). PR body must say `Closes #N`.

## Stuck?

- Blocked by another issue: `python tools/task.py block N "waiting on #M: <why>"`, then pick other work.
- Out of time: `note` with done/left/next command, then `done --draft` or `release N`.
- Found extra work: `python tools/task.py new "title" --area A --priority p2`. Do not do it in this branch.
- Needs a `contracts.py` change: stop, file/flag a `contract-change` issue, follow docs/COLLABORATION.md.
