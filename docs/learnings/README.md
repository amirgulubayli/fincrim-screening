# Learnings

One file per learning, so parallel agents never conflict: `docs/learnings/<YYYY-MM-DD>-<slug>.md` (e.g. `2026-11-21-slash-dates-are-day-first.md`). Never edit someone else's file; add a new one that links to it.

Never paste data-room records. Describe patterns with synthetic or paraphrased examples.

## Template

```markdown
# <one-line finding>

- Date / author: 2026-11-21 / claude (#12)
- Area: rules | engine | data | dashboard | demo | docs | infra
- Affects: system/screening/rules/dob.py

## What we found
2-4 sentences. Pattern in the data or behaviour of the engine.

## Evidence
Counts or eval numbers (e.g. "18 hits in dev; eval -v shows ..."). Hit IDs are fine; record contents are not.

## What to do
Concrete instruction for the next agent, or the issue filed (`#N`).
```
