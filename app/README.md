# Analyst dashboard (demo layer, not scored)

FastAPI server (`app/server`) that runs the stdlib engine in `system/` once on startup and serves
results, plus a Vite + React UI (`app/web`) with a review queue, hit detail, analyst overrides
(written to `out/reviews.jsonl`, gitignored) and a live `screen ...` terminal.

The data folder comes from `SCREEN_DATA` (default `data/dev`, relative to the repo root).
Startup loads the dataset (~15 s) before the API answers.

## Dev (two terminals, from the repo root)

```powershell
# 1. API on :8000
$env:SCREEN_DATA = "data/dev"
uv run uvicorn app.server.main:app --port 8000 --reload

# 2. UI on :5173 (proxies /api, including the terminal WebSocket, to :8000)
cd app/web
npm install
npm run dev
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
| GET | `/api/summary` | counts by decision / tier, dataset counts, timings, eval vs practice labels |
| GET | `/api/decisions?tier=&decision=&q=` | queue rows |
| GET | `/api/hits/{hit_id}` | decision + trace, raw hit, alert, resolved customer records / KYC / notes, reviews |
| POST | `/api/hits/{hit_id}/review` | `{decision: CONFIRM\|SAME_ENTITY\|DIFFERENT_ENTITY\|REVIEW, note, analyst}` |
| POST | `/api/run` | re-run the engine |
| WS | `/api/terminal` | send one command line; only `screen run\|explain\|eval\|repro\|summary ...`, no shell metacharacters; runs `python system/screen.py ...` without a shell |

Terminal example: `screen eval --data data/dev` (use forward slashes in paths; backslashes are rejected).
