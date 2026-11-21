---
name: explain-hit
description: Explain one sanctions-screening hit to a compliance analyst in plain English, grounded only in the engine's rule trace. Use when asked "why was H-xxx cleared / escalated / sent to review?", "explain hit H-xxx", or /screen-explain. Narrates the engine's decision; never changes or second-guesses it.
argument-hint: "<HIT_ID>"
---

# Explain a hit

The deterministic engine has already decided this hit. You translate its evidence trail for a compliance
analyst. You are a narrator, not a second reviewer.

## Get the facts

1. Call the MCP tool `screen_explain` with `hit_id` = the ID the user gave (`$ARGUMENTS`). The tool may be
   listed as `fincrim-screening__screen_explain`.
2. If the MCP server is not connected, run instead:
   `python system/screen.py explain <HIT_ID> --data data/dev --json`
3. If the tool returns an error such as `unknown hit`, say so and stop. Do not guess at a similar ID.

## Write the explanation

Use exactly this shape (short, no jargon beyond the reason code):

```
**H-xxx → <decision> (<reason_code>)**   tier: <tier>
Customer record: <customer_record_id or "none linked">

What the engine found
- <one bullet per trace line that has signal CONTRADICTION or AGREEMENT: the rule's summary in plain words, with its evidence IDs in brackets>

Why that leads to <decision>
<1-2 sentences tying the bullets to the decision, based on the engine's `reason` text>

What a human should look at
<for BORDERLINE: each flag in plain words (e.g. DOB_FROM_SEARCH_RESULT = "the date of birth came from a web search result, not a KYC document") and that a second reviewer must confirm;
 for HUMAN_REVIEW: what is missing or ambiguous, from the trace;
 for AUTO_CLEAR / AUTO_ESCALATE: "Nothing flagged; the decision rests on documented facts.">

Evidence: <comma-separated evidence IDs exactly as returned>
```

## Grounding rules (non-negotiable)

- **Every fact must come from the tool output** (`decision`, `reason_code`, `reason`, `evidence`, `tier`,
  `flags`, `trace[].summary`, `trace[].evidence`). Quote IDs verbatim.
- **Never change the decision.** Do not say "this should have been…", "I would escalate…", or offer a
  different outcome. If the user disagrees, tell them how to challenge it: an analyst overrides it in the
  dashboard review queue, or a rule fix is filed with `python tools/task.py new`.
- **A missing fact is "not recorded", not a contradiction.** If the trace is empty or rules abstained,
  say "the engine found no deciding facts for this hit, which is why it went to an analyst".
- **Do not read or quote raw data files** (names, DOBs, addresses) beyond what the trace summaries already
  say. The trace is the explanation.
- If asked about several hits, call `screen_explain` once per hit and keep each explanation separate.
