# AGENTS.md - protocol for every agent and human

Canonical rules for Claude Code, Grok Build, Cursor, Codex and people. Keep this file under 8,500 characters (Grok truncates at 10k).

Project: "Is my customer sanctioned?" For every screening hit, output DIFFERENT_ENTITY / SAME_ENTITY / REVIEW + one reason code + customer_record_id + 2-4 sentence reason + evidence IDs. Scored entry point: `system/run <data folder> <output file>`. The SOP is `data/dev/POLICY.md` (local only, gitignored).

## Golden rules

1. **Never commit data or keys.** Nothing from `data/` except `data/README.md`; no `.xlsx`, no `.env`, no API keys. The repo goes public. If you see data in a diff, stop and remove it.
2. **Standard library only in `system/`.** No pip packages, no network, read only the data folder given. `app/` and `tools/` may use deps.
3. **Never weaken safety.** Clearing a true SAME/REVIEW hit costs 60; a needless REVIEW costs 2-3. When in doubt, REVIEW. A missing fact is never a contradiction. Exceptions become REVIEW (`pipeline.fallback`), never a clear.
4. **Deterministic.** In `system/`: no randomness, no clock in decisions, no network, no LLM, no iteration over unsorted sets/dicts built from sets. Sort by ID. `repro --runs 5` must stay byte-identical.
5. **Stay inside your claimed issue.** Touch only the files it needs. Found other work? `python tools/task.py new "..." --area A`.
6. **One issue = one branch = one PR.** Branch `<issue#>-<slug>`, PR body `Closes #N`.
7. **Contracts are shared.** Any change to `system/screening/contracts.py` public names/fields needs the `contract-change` label and a note on who is affected.
8. **LLM never decides.** LLMs (Grok via OpenRouter, key in env `OPENROUTER_API_KEY`) are for dev agents, dashboard narrative and the morning bot only.

## Task loop

```
python tools/task.py list [--area rules]       # pick a status:ready issue
python tools/task.py claim N --agent claude     # labels + branch N-slug, checks it out
# build: code + tests in tests/
uv run pytest -q
python system/screen.py eval --data data/dev -v # MUST show unsafe_clearances: 0 (exit 0)
python system/screen.py repro --data data/dev --runs 5   # byte_identical: true
python tools/task.py note "what changed, eval numbers, open questions"
python tools/task.py done                       # push + PR "Closes #N" (--draft if unfinished)
```

Other: `task.py status` (your current issue), `task.py sync` (rebase on main), `task.py release [N]` (give it back), `task.py block N "reason"`, `task.py new "title" --area A --priority p1`, `task.py list --all`.

Labels: `area:{engine,rules,data,dashboard,demo,docs,infra}`, `status:{ready,claimed,in-review,blocked}`, `agent:{claude,grok,cursor,codex,human}`, `priority:{p0,p1,p2}`, `contract-change`.

## Adding or finishing a rule

Rules live in `system/screening/rules/`, one file per facet. Copy `rules/entity_type.py` (the reference):

- `RULE_ID = "P6.<facet>"` (SOP principle number + facet).
- `def check(ctx: Context) -> list[RuleResult]` - pure; return `[]` when unsure.
- Emit `Signal.CONTRADICTION` with a DIFFERENT reason code, `Signal.AGREEMENT` (no reason needed; the compiler picks the SAME code), or `Signal.NEUTRAL` for the trace.
- `summary`: one plain sentence with the decisive facts (it becomes the reason text).
- `evidence`: only the record IDs that decided it (hit, customer, KYC check, NOTE, SR...).
- Shaky fact -> `weak=True, weak_why="DOB_FROM_SEARCH_RESULT"` etc. (routes to BORDERLINE, see docs/HITL.md).
- Register new modules in `rules/__init__.py` `RULES`. Do not touch `compiler.py` precedence without an issue.
- Normalisation (names, dates, countries) goes in `system/screening/normalize/`, not in rules.
- Add tests with tiny hand-built records (never copy real data into tests).

## Where things live

| Path | What |
|---|---|
| `system/run`, `system/screen.py` | scored entry point, CLI |
| `system/screening/contracts.py` | enums, records, RuleResult, HitDecision (shared) |
| `system/screening/loaders.py` | read data folder -> Dataset |
| `system/screening/resolve.py` | who is the customer (SOP 2-4) |
| `system/screening/facts.py` | hit facts (SOP 5) |
| `system/screening/rules/` | one check per facet (SOP 6-8) |
| `system/screening/compiler.py` | fixed precedence -> decision |
| `system/screening/triage.py` | HITL tier + weak flags |
| `system/screening/explain.py` | template reason + evidence |
| `system/screening/evaluate.py` | organiser-style cost scoring |
| `tools/` | task.py, bootstrap_github.py, import_xlsx.py |
| `app/` | demo server + dashboard (not scored) |
| `docs/` | ARCHITECTURE, COLLABORATION, HITL, learnings/ |
| `output/dev.jsonl` | committed submission output |

## Commands

```
python system/screen.py run data/dev output/dev.jsonl [--trace output/trace.jsonl]
python system/screen.py explain <HIT_ID> --data data/dev [--json]
python system/screen.py eval --data data/dev [-v] [--json]   # exit 1 on any unsafe clearance
python system/screen.py repro --data data/dev --runs 5 [--json]
python system/screen.py summary --data data/dev [--json]
```

## Handoff

Stopping mid-task? Run `task.py note` with: what is done, what is left, failing tests, next command to run. Then `task.py done --draft` (keeps the PR open) or `task.py release`. The next agent reads the issue comments first.

## Avoiding merge conflicts

- Each rule/normaliser is its own file; stay in yours.
- `task.py sync` before starting and before `done`.
- Learnings go in `docs/learnings/<YYYY-MM-DD>-<slug>.md`, one new file each. Never append to a shared notes file.
- Do not reformat files you did not change. Do not regenerate `output/dev.jsonl` in feature PRs (only on the release step).
- `uv.lock`, `pyproject.toml`, `contracts.py`: change only when the issue says so.

## Definition of done

Tests pass, eval shows 0 unsafe clearances, repro byte-identical, no data/keys in diff, `system/` still stdlib-only, issue note written, PR says `Closes #N`. More: docs/COLLABORATION.md.
