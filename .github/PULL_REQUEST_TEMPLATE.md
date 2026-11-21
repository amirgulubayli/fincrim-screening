Closes #

## What changed
<!-- 1-3 bullets. Which SOP principle(s) does this implement? -->

## Agent / author
<!-- human | claude | grok | cursor | codex (and who reviewed the agent's work) -->

## Evidence it works
<!-- paste the outputs -->
- [ ] `uv run pytest -q` passes
- [ ] `python system/screen.py eval --data data/dev` shows **0 unsafe clearances** (paste decision_score before -> after)
- [ ] `python system/screen.py repro --data data/dev --runs 5` is byte-identical
- [ ] No new imports outside the standard library in `system/`
- [ ] No data, keys or SOP text committed

```
eval output here
```

## Contract change?
- [ ] No
- [ ] Yes: added the `contract-change` label and listed who is affected:
