# Run this with your Grok Bot

The repo is packaged as a **Grok plugin** (`.grok/plugins/fincrim-screening/`). Grok gets MCP tools to
start the engine and the review dashboard (which has the `screen` CLI embedded as a terminal), run
and explain screenings, show review queues and post a morning report. Grok only operates the system.
Every decision comes from the deterministic engine.

## 1. Give the bot the repo and the data (one-time)

| What | How |
|---|---|
| **Repo** | The bot's machine needs GitHub access: `gh auth login` with an account that can read `amirgulubayli/fincrim-screening` (invite it as a collaborator), then `gh repo clone amirgulubayli/fincrim-screening`. After submission the repo is public and no login is needed. |
| **Data** | Never in git. Copy the data-room folder to `fincrim-screening/data/dev/`, or give the bot the workbook `Screening Dev Set - browse.xlsx` (the launcher rebuilds `data/dev` from it). |
| **Tools** | Python 3.10+, [uv](https://docs.astral.sh/uv/), Node.js 20+. Check with `python tools/launch.py doctor`. |
| **Plugin** | From the repo root: `grok plugin install ./.grok/plugins/fincrim-screening --trust` (or in the Grok TUI: `/plugins`, select `fincrim-screening`, enable and trust it). Then `grok inspect` should list it as enabled. |
| **Login** | `grok login` once on that machine. |

## 2. Paste this into your Grok Bot channel

```text
You have the fincrim-screening plugin. Working directory: the fincrim-screening repo.
1) Run the `doctor` tool and tell me if anything is missing.
2) Start the system with `dashboard_up` (data: data/dev; if that folder is empty use
   xlsx: "<path to Screening Dev Set - browse.xlsx>"). Wait until it's ready.
3) Reply with the dashboard URL and the headline numbers: hits, tiers, engine agreement,
   unsafe clearances and decision score on the practice labels.
4) Then explain H-58A39C (a clearance), H-4D76E3 (a confirmed match) and H-B1018D (escalated to
   a human) with `screen_explain`. For each one give the decision, the facts it rests on, the
   evidence IDs and any "Reviewer:" action. Don't add facts the tool didn't return.
Every morning at 08:00, run the morning-run skill and post the report here.
```

Slash commands are also available: `/screen-up [data|workbook]`, `/screen-explain <hit>`,
`/screen-queue <tier>`, `/screen-morning`.

## 3. Without Grok (same thing, by hand)

```bash
python tools/launch.py up --data data/dev     # or: --xlsx "Screening Dev Set - browse.xlsx"
python tools/launch.py status
python tools/launch.py down
```

Open the printed URL. The bottom panel is a live terminal: `screen eval`, `screen explain H-58A39C`,
`screen queue --tier BORDERLINE`, `screen diff`, `screen policy`.

## Viewing it from another device (optional, be careful)

The server listens on `127.0.0.1` only, because the dashboard shows customer data. For a live demo on
another screen you can either:
- run `python tools/launch.py up --host 0.0.0.0` on a trusted network, or
- put an authenticated tunnel in front of it (for example Cloudflare Tunnel with Access).

Never expose it without authentication.
