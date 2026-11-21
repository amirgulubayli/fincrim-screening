# Analyst dashboard (demo layer, not scored)

FastAPI server (`app/server`) that runs the stdlib engine in `system/` once on startup and serves
results, plus a Vite + React UI (`app/web`):

- **Review queue** by tier, worked in the order a person needs them: *Needs analyst review*,
  *Borderline — 4-eyes check*, *Auto-escalated to compliance*, *Auto-cleared (hard contradiction)*.
  Each row has a one-line "why this tier". Filter by reason code, weak-evidence flag and case status.
- **Case file** per hit: decision + reason code + reason, customer vs watchlist comparison with
  agree / contradict / not decisive / not checked markers taken from the rule trace, the resolution
  path, evidence chips that open the underlying record, and the rule trace as a timeline.
- **Analyst actions** (accept or override) always need a name and a note. BORDERLINE cases, and any
  analyst clearance of a hit the engine did not clear, need a second, different analyst (4-eyes).
  Actions append to `out/reviews.jsonl` (gitignored); the engine output is never changed.
- **Audit trail** view (who / when / what / note / case status) with CSV export.
- **Policy & controls**: SOP principles (paraphrased) mapped to rule ids, with per-rule counts.
- Header KPIs: automation rate, open cases, unsafe clearances on practice labels, reproducibility badge.
- Keyboard: `j`/`k` next/prev hit, `1`-`4` tiers, `0` all, `/` search, `Esc` leaves a field.
- A live `screen ...` terminal.

The data folder comes from `SCREEN_DATA` (default `data/dev`; relative paths resolve from the repo
root, absolute Windows paths are fine). Startup loads the dataset (~15 s) before the API answers.

## Dev (two terminals, from the repo root)

```powershell
# 1. API on :8000
$env:SCREEN_DATA = "C:/path/to/data/dev"
uv run uvicorn app.server.main:app --port 8000 --reload

# 2. UI on :5173 (proxies /api, including the terminal WebSocket, to :8000)
cd app/web
npm install
npm run dev          # API_PORT=8014 / WEB_PORT=5194 to run beside another checkout
```

Open http://localhost:5173.

## Prod / demo (one process)

```powershell
cd app/web; npm install; npm run build; cd ../..
uv run uvicorn app.server.main:app --port 8000
```

Open http://localhost:8000 (the server serves `app/web/dist` when it exists).

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | ready flag |
| GET | `/api/summary` | counts by decision / tier / reason / flag, rule stats, automation rate, case status, eval vs practice labels, repro state |
| GET | `/api/decisions?tier=&decision=&q=` | queue rows (with case status and latest analyst decision) |
| GET | `/api/hits/{hit_id}` | decision + trace, raw hit, alert, resolved customer records / KYC / notes, reviews, case status |
| GET | `/api/records/{id}` | read-only lookup of any record id (customer, KYC check, note, search result, hit, alert, account, KYC applicant) |
| POST | `/api/hits/{hit_id}/review` | `{decision: CONFIRM\|SAME_ENTITY\|DIFFERENT_ENTITY\|REVIEW, note, analyst}`; note and analyst required |
| GET | `/api/reviews` | audit trail, newest first |
| GET | `/api/reviews.csv` | audit trail export |
| GET / POST | `/api/repro` | read `output/repro/report.json` / start a 5-run `screen repro` in the background |
| POST | `/api/run` | re-run the engine |
| WS | `/api/terminal` | one command line at a time; only `screen run\|explain\|eval\|repro\|summary ...`, no shell metacharacters; runs `python system/screen.py ...` without a shell |

Terminal: `--data` defaults to the server's `SCREEN_DATA`, so `screen eval`, `screen summary` and
`screen explain H-123456` need no path; `screen run` writes `output/terminal_run.jsonl`. Backslashes
in paths are accepted. Output files must stay under `output/` or `out/`. Ctrl+C (or the Stop
button) kills a running command; commands are killed after `SCREEN_TERMINAL_TIMEOUT` seconds (240).
