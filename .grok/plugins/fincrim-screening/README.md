# fincrim-screening: a Grok plugin

Ask Grok "why was H-xxx cleared?" and get an answer grounded in the screening engine's rule trace, and get a
daily health check posted to GitHub without anyone touching the code.

The engine in `system/` is deterministic (no LLM, no network, byte-identical across runs) and makes every
decision. This plugin gives Grok **read-only** access to that engine. **Grok narrates; it never decides.**
No tool can change a decision, write to the data folder or edit code.

## Layout

```
.grok/plugins/fincrim-screening/
├── .claude-plugin/plugin.json   manifest (Claude-Code-compatible layout, `grok plugin validate` passes)
├── .mcp.json                    stdio MCP server: python ${CLAUDE_PLUGIN_ROOT}/mcp/server.py
├── mcp/server.py                MCP server, Python standard library only, JSON-RPC 2.0 over stdio
├── skills/
│   ├── morning-run/SKILL.md     daily health check -> out/morning/YYYY-MM-DD.md + GitHub issue
│   └── explain-hit/SKILL.md     compliance-analyst explanation of one hit, from the trace only
├── commands/
│   ├── screen-morning.md        /screen-morning [data]
│   ├── screen-explain.md        /screen-explain <HIT_ID>
│   └── screen-queue.md          /screen-queue [tier] [limit]
└── README.md
```

## MCP tools

| Tool | Arguments | Returns |
|---|---|---|
| `screen_summary` | `data?` | counts of alerts, hits, customers, accounts, KYC checks, notes, search results, labels |
| `screen_run` | `data?` | counts by decision, HITL tier, decision x tier, reason code, flag |
| `screen_explain` | `hit_id`, `data?` | decision, reason code, customer record, reason, evidence IDs, tier, flags, full trace (`trace[0]` = decision core `CORE.aml`), plus `core_verdict`, `check_verdict`, `agreement` (AGREE / DISAGREE / CONFLICT / UNAVAILABLE), `escalated_by_policy`, `reviewer_action`, `policies` (ids from `policies_for`) |
| `engine_diff` | `agreement?`, `limit?` (50), `data?` | dual control: counts by core/check decision pair, agreement counts and rate, hits escalated by policy, list of hits that differ |
| `policy_list` | `kind?` (sop / hofc / dual_control) | policy registry from `system/screening/policy.py` (`registry()`, else `POLICIES`); built-in HoFC list from `contrib/aml-bot/README.md` if the module is missing |
| `review_queue` | `tier?` (HUMAN_REVIEW), `limit?` (20), `data?` | hits in that tier, sorted by ID |
| `screen_eval` | `limit?` (25), `data?` | practice-label score: decision score, accuracy, review rate, **unsafe clearances + hit IDs**, mismatches |
| `screen_repro` | `runs?` (5), `data?` | N fresh-process runs with different PYTHONHASHSEED: sha256 each, `byte_identical`, unstable hits |

Dual control: the engine runs a **decision core** (decides) and an **independent rule check** (second opinion).
The server records each check verdict while the pipeline runs (one pass; decisions are exactly `screen run`'s),
so `screen_explain` / `engine_diff` can show both. The `explain-hit` skill narrates core vs check and always
surfaces the `Reviewer: ...` instruction verbatim.

Grok shows them as `fincrim-screening__<tool>`. The server imports the engine in-process (repo `system/` on
`sys.path`) and caches the run until a data file changes; `screen_repro` uses subprocesses because proving
determinism needs fresh interpreters. The `initialize` result carries `instructions` telling the model the
engine's output is final.

Environment: `SCREEN_DATA` (default `data/dev`, relative to the repo root), `SCREEN_REPO` (default: the repo
containing the plugin, else the nearest parent of the working directory that has `system/screening/`).

## Enable it (one-time, per machine)

Grok discovers the plugin automatically (`grok inspect` lists `fincrim-screening (project, disabled) 2 skills,
1 MCPs`). Project plugins are a security boundary: Grok will not run a repo's MCP server until you trust it.
`.grok/config.toml` already lists the plugin under `[plugins] enabled`; you still have to trust it, either:

```
grok plugin install ./.grok/plugins/fincrim-screening --trust     # from the repo root
```

or, in the Grok TUI, `/plugins`, select `fincrim-screening`, press `Space` to enable and trust it when asked.
Then check: `grok inspect` should show it `enabled`, with the skills `morning-run` and `explain-hit` and the
MCP server `fincrim-screening`.

## Use it

```
/screen-explain H-5EEC5A          # or just ask: "why was H-5EEC5A cleared?"
/screen-queue BORDERLINE 10
/screen-morning                   # eval + repro + queue -> out/morning/<date>.md + issue "Morning run <date>"
```

Headless / scheduled (no TUI): `powershell -File tools/morning_run.ps1` runs `tools/morning_run.py`
(deterministic report, works without Grok) and then `grok -p "/morning-run ..." --output-format json` with
the file-editing tool removed and only `gh issue`, `task.py new` and `screen.py` auto-approved. Schedule with
Windows Task Scheduler; see the header of `tools/morning_run.ps1` for the `schtasks /create` line.

## Test it without Grok

```
uv run pytest tests/test_mcp_server.py -q
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python .grok/plugins/fincrim-screening/mcp/server.py
```

## Guardrails

- Read-only: no tool writes to `data/`, `system/` or anywhere else (repro writes to a temp dir).
- The skills forbid editing, committing or pushing, and forbid suggesting a different decision.
- The morning report is posted unchanged; Grok's narrative goes in a separate, labelled comment.
- Issues contain hit IDs and numbers only, never customer names, dates of birth or other data fields.
