# fincrim-screening

Track: **"Is my customer sanctioned?"** (Diligent). A screening tool compares customers with sanctions, PEP, enforcement and adverse-media lists and raises hits on similar names. For every hit this engine decides `DIFFERENT_ENTITY` (clear), `SAME_ENTITY` (escalate) or `REVIEW` (a person looks), with one reason code, the resolved customer record, a 2-4 sentence reason and the evidence IDs it relied on. Decisions are deterministic, rule-based (one rule per SOP principle) and fully traceable; no LLM sits in the decision path. Every decision is also routed to a human-in-the-loop tier:

| Tier | When | Who acts |
|---|---|---|
| `AUTO_CLEAR` | DIFFERENT_ENTITY on a solid contradiction | nobody (sampled QA) |
| `AUTO_ESCALATE` | SAME_ENTITY on solid agreement | compliance team |
| `BORDERLINE` | automated decision resting on weak evidence | 4-eyes QA |
| `HUMAN_REVIEW` | SOP says REVIEW | analyst queue |

Details: [docs/HITL.md](docs/HITL.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), judge-facing [system/README.md](system/README.md).

## 5-minute setup

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install GitHub.cli ; gh auth login
gh repo clone amirgulubayli/fincrim-screening ; cd fincrim-screening
# data: copy the data-room files into data\dev\  (see data/README.md), or:
uv run --with openpyxl python tools/import_xlsx.py "path\to\Screening Dev Set - browse.xlsx"
uv sync
python system/screen.py run data/dev output/dev.jsonl
python system/screen.py eval --data data/dev -v
python system/screen.py repro --data data/dev --runs 5
```

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install gh   # or your package manager
gh auth login
gh repo clone amirgulubayli/fincrim-screening && cd fincrim-screening
# data: copy files into data/dev/ or run tools/import_xlsx.py as above
uv sync
python3 system/screen.py run data/dev output/dev.jsonl
python3 system/screen.py eval --data data/dev -v
python3 system/screen.py repro --data data/dev --runs 5
```

`eval` exits 1 if any true SAME/REVIEW hit was cleared. `data/` is gitignored: never commit it.

## Working with agents

All agents follow [AGENTS.md](AGENTS.md). Tasks are GitHub issues driven by `tools/task.py`. Copy-paste one of these:

**Claude Code**
```
claude "Use the team-task skill. Run python tools/task.py list --area rules, claim the top ready issue with --agent claude, implement it with tests, make eval show 0 unsafe clearances, then note and done."
```

**Grok Build**
```
grok "Read AGENTS.md. Run python tools/task.py list, claim the highest-priority ready issue with --agent grok, implement it with tests, run eval and repro, then python tools/task.py note and done."
```

**Cursor** (Agent chat)
```
Follow AGENTS.md. Claim issue #N with `python tools/task.py claim N --agent cursor`, implement it, run `python system/screen.py eval --data data/dev`, then `python tools/task.py done`.
```

Human? Same loop: `python tools/task.py list`, `claim N --agent human`, `done`. Full protocol: [docs/COLLABORATION.md](docs/COLLABORATION.md).

## Repo map

| Path | What |
|---|---|
| `system/` | scored engine, Python stdlib only (`run`, `screen.py`, `screening/`) |
| `system/screening/rules/` | one SOP check per facet |
| `output/dev.jsonl` | committed output on the dev set |
| `tools/` | `task.py` (coordination), `bootstrap_github.py`, `import_xlsx.py` |
| `app/` | demo: FastAPI server + Vite React dashboard (not scored) |
| `docs/` | architecture, collaboration, HITL, learnings |
| `tests/` | pytest suite (synthetic records only) |
| `data/` | local data, gitignored |

## Invite a teammate

```
gh api -X PUT repos/amirgulubayli/fincrim-screening/collaborators/USERNAME -f permission=push
```

## Submission checklist

- [ ] `python system/screen.py run data/dev output/dev.jsonl` regenerated on final `main` and committed
- [ ] `eval` shows 0 unsafe clearances; `repro --runs 5` byte-identical
- [ ] `system/README.md` complete (architecture, examples, time and cost)
- [ ] `system/` stdlib-only, runs in < 30 min, reads only the data folder
- [ ] No data, `.xlsx`, `.env` or keys anywhere in history: `git log --all --stat | grep -Ei "data/dev|\.xlsx|\.env"` is empty
- [ ] Repo flipped public: `gh repo edit amirgulubayli/fincrim-screening --visibility public --accept-visibility-change-consequences`
