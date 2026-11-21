---
name: launch-dashboard
description: >
  Bring up the sanctions-screening system for a person: engine + review dashboard (with the CLI
  embedded as a terminal), then share the URL. Use when asked to "start/spin up/open the dashboard",
  "run the screening system", "launch the UI", "/screen-up", or to stop it ("shut it down").
metadata:
  short-description: "Start engine + dashboard and share the URL"
---

# Launch the screening dashboard

You operate the system; you never decide screening outcomes yourself. Every decision shown comes
from the deterministic engine (decision core + independent check).

1. **Check the machine**: call the `doctor` tool. If `uv` or `npm` is missing, tell the user the one
   install step (uv: https://docs.astral.sh/uv/ ; Node.js 20+) and stop.
2. **Check the data**: the engine needs the data-room files (alerts.jsonl, hits.jsonl, customers.jsonl,
   ...) in `data/dev` (or a folder the user names). Data is never in git. If the user only has the
   workbook "Screening Dev Set - browse.xlsx", pass it as `xlsx` and the data is rebuilt.
3. **Start it**: call `dashboard_up` (optionally `data`, `xlsx`, `port`). It installs deps, builds the
   UI, starts the server and waits ~2 minutes until both engines have screened every hit.
4. **Report** in the channel, briefly:
   - the URL (http://127.0.0.1:PORT/) and that the terminal panel at the bottom runs `screen ...` commands
     (e.g. `screen eval`, `screen explain H-58A39C`, `screen queue --tier BORDERLINE`, `screen diff`);
   - headline numbers from `screen_run` / `screen_eval`: hits, tiers, unsafe clearances, decision score.
5. **Status / stop**: `dashboard_status` to check; `dashboard_down` when asked to shut it down.

Never commit data, never push to git, never edit code from this skill. If `dashboard_up` fails, read the
log path it returns and report the last error lines verbatim.
