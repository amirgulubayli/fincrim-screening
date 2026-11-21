# 3-minute demo script (compliance judges)

**Story in one line:** every screening hit gets a decision a regulator can audit: same answer every run,
every decision traceable to a rule and a record ID, the machine only clears what the SOP lets it clear,
and Grok explains but never decides.

> **Fill before demo day.** Hit IDs below are placeholders (`H-CLEAR`, `H-ESC`, `H-BORDER`, `H-REVIEW`)
> until the rules land. Pick them with the commands in [Picking the showcase hits](#picking-the-showcase-hits)
> and check each one's `explain` output reads cleanly. Paste the real IDs and numbers into this file.

## Setup (before the judges arrive)

| What | Command / action |
|---|---|
| Dashboard | `uv run uvicorn app.server.main:app --port 8000` (built UI) - open <http://localhost:8000> |
| Fresh morning report | `powershell -File tools/morning_run.ps1` (or `python tools/morning_run.py`) - issue "Morning run YYYY-MM-DD" is open in a browser tab |
| Grok | `grok` TUI open in the repo, plugin trusted (`grok inspect` shows `fincrim-screening` enabled) |
| Fallback | screenshots of every step in `docs/demo/` (take them during the dry run); if live fails, click through them |
| Font size | terminal + browser zoomed so the back row can read a hit ID |

## Script

| Time | Show | Say |
|---|---|---|
| 0:00-0:20 | Title slide / dashboard home | "A bank screens customers against sanctions lists. Most hits are false positives, but clearing a real one is the expensive mistake: the brief prices it at 60 times a needless review. We built an engine that clears only what the SOP allows, and shows its working." |
| 0:20-0:45 | Morning-run issue on GitHub | "Every morning a bot runs the health check and posts this. Decision score **[SCORE]**, **0 unsafe clearances**, 5 runs byte-identical. The numbers come from the deterministic engine; Grok only wrote the summary comment underneath." |
| 0:45-1:00 | Dashboard terminal: `screen eval --data data/dev` | "Same check live. The line that matters: `unsafe_clearances: 0`. The command fails the build if that is ever not zero." |
| 1:00-1:20 | Queue, open **AUTO_CLEAR** `H-CLEAR` | "Cleared automatically. The trace shows one solid contradiction: [e.g. the customer was born in 1971, the listed person in 1958, from approved KYC check KYC-xxx]. Evidence IDs are clickable records." |
| 1:20-1:40 | Open **AUTO_ESCALATE** `H-ESC` | "Name and date of birth agree and nothing contradicts. The engine does not guess: it escalates to the compliance team with the matching facts." |
| 1:40-2:00 | Open **BORDERLINE** `H-BORDER` | "The engine reached a decision, but the deciding fact is weak: flag `[FLAG]` means [e.g. the DOB came from a web search, not a KYC document]. So a second reviewer confirms it. Automation where it is safe, four eyes where it is not." |
| 2:00-2:15 | Open **HUMAN_REVIEW** `H-REVIEW` | "Here nothing settles it [e.g. two customers share the account and disagree]. The SOP says review, so a person decides. When in doubt, review." |
| 2:15-2:35 | Terminal: `screen repro --data data/dev --runs 5` | "Five fresh processes, different hash seeds, identical SHA-256. Same input, same decision, every time: that is what an auditor needs." |
| 2:35-2:55 | Grok TUI: *"why was H-CLEAR cleared?"* | "Grok, our sponsor's agent, calls the plugin's `screen_explain` tool and turns the rule trace into plain English for the analyst. Every fact it quotes is from the trace; it is not allowed to change or second-guess the decision." |
| 2:55-3:00 | Back to the morning issue | "Deterministic engine, human-in-the-loop tiers, and an LLM that explains but never decides." |

## Exact commands

```
# dashboard terminal (only `screen ...` commands are accepted there)
screen eval --data data/dev
screen explain H-CLEAR --data data/dev
screen repro --data data/dev --runs 5

# same from a shell
python system/screen.py eval --data data/dev
python system/screen.py explain H-CLEAR --data data/dev
python system/screen.py repro --data data/dev --runs 5

# Grok
grok                                  # then: why was H-CLEAR cleared?   or   /screen-explain H-CLEAR
/screen-queue BORDERLINE 5
```

## Picking the showcase hits

Choose hits whose `explain` output is short, has evidence IDs and no `ENGINE_ERROR` flag. Good stories: a
joint-account pick, an identity-graph merge, a DOB taken from a support note.

```
python tools/morning_run.py --no-repro --queue-limit 50     # lists HUMAN_REVIEW and BORDERLINE hits
python system/screen.py run data/dev out/demo.jsonl --trace out/demo_trace.jsonl
python -c "import json; [print(r['tier'], r['hit_id'], r['reason_code'], r['flags']) for r in map(json.loads, open('out/demo_trace.jsonl'))]" | sort | head -80
python system/screen.py explain <HIT> --data data/dev        # read it aloud; is it clear in 15 seconds?
```

Or ask Grok: `review_queue` with `tier: AUTO_CLEAR`, then `/screen-explain` on a candidate.

| Slot | Hit ID | Decision / reason code | One-line story |
|---|---|---|---|
| AUTO_CLEAR | `H-CLEAR` | DIFFERENT_ENTITY / [code] | [fill] |
| AUTO_ESCALATE | `H-ESC` | SAME_ENTITY / [code] | [fill] |
| BORDERLINE | `H-BORDER` | [decision] / [code], flag [FLAG] | [fill] |
| HUMAN_REVIEW | `H-REVIEW` | REVIEW / [code] | [fill] |

## If something breaks

| Failure | Recovery |
|---|---|
| Dashboard down | run the same `python system/screen.py ...` commands in a plain terminal |
| Grok not signed in / no network | show the `explain` output and the morning issue's Grok comment from the dry run; say "the plugin is offline, the engine is not" |
| repro slow | it takes ~10 s per run on dev; start it at 2:00 while talking about HUMAN_REVIEW |
| Anything else | fallback screenshots in `docs/demo/` |
