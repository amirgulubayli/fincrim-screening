---
name: explain-hit
description: Explain one sanctions-screening hit to a compliance analyst in plain English, grounded only in the engine's output - the decision core's verdict, the independent rule check's facet trace, and the reviewer action. Use when asked "why was H-xxx cleared / escalated / sent to review?", "explain hit H-xxx", "do the engines agree on H-xxx?", or /screen-explain. Narrates the engine's decision; never changes or second-guesses it.
argument-hint: "<HIT_ID>"
---

# Explain a hit

The deterministic engine has already decided this hit. It runs **two independent engines** (dual control):

- the **decision core** (the reviewed AML Bot engine) **decides**;
- the **independent rule check** (modular, facet by facet) runs alongside as a second opinion.

When they differ, the engine never clears on a conflict: core clears but check confirms -> REVIEW
(`ENGINES_CONFLICT`); any other difference -> 4-eyes check (`ENGINES_DISAGREE`). Some hits are kept in review
by a Head of Financial Crime policy call (`ESCALATED_BY_POLICY`), and the core then writes a
`Reviewer: ...` instruction for the human.

You translate this for a compliance analyst. You are a narrator, not a second reviewer.

## Get the facts

1. Call the MCP tool `screen_explain` with `hit_id` = the ID the user gave (`$ARGUMENTS`). The tool may be
   listed as `fincrim-screening__screen_explain`. Fields you use: `decision`, `reason_code`, `reason`,
   `evidence`, `tier`, `flags`, `trace` (`trace[0]` has `rule_id` `CORE.aml` = the decision core; the rest are
   the check's facet results), `core_verdict`, `check_verdict`, `agreement`, `escalated_by_policy`,
   `reviewer_action`.
2. If the MCP server is not connected, run instead:
   `python system/screen.py explain <HIT_ID> --data data/dev --json` (add `--engine` if that flag exists).
   That output has no `core_verdict` / `check_verdict` / `reviewer_action` fields: take the core from the
   `CORE.aml` trace row, and copy the reviewer action from the text after `Reviewer:` in `reason`, if any.
3. If the tool returns an error such as `unknown hit`, say so and stop. Do not guess at a similar ID.

## Write the explanation

Use exactly this shape (short, no jargon beyond the reason code):

```
**H-xxx → <decision> (<reason_code>)**   tier: <tier>
Customer record: <customer_record_id or "none linked">

Reviewer action: <reviewer_action, verbatim>          <- ONLY when reviewer_action is not null; always first

Two engines: <AGREE | DISAGREE | CONFLICT | only one engine ran>
- Decision core (decides): <core_verdict.decision> (<core_verdict.reason_code>) - <one sentence from core_verdict.reason, without the "Reviewer:" part> [<core evidence IDs>]
- Independent check: <check_verdict.decision> (<check_verdict.reason_code>)
  - <one bullet per check trace row with signal contradiction or agreement: facet, its summary in plain words, [evidence IDs]>

Why that leads to <decision>
<1-2 sentences. AGREE: both engines reached it independently. DISAGREE: the core decides and a second analyst
 must confirm. CONFLICT: the core cleared it but the check confirms the match, so it is NOT cleared and goes to an
 analyst. ESCALATED_BY_POLICY: a Head of Financial Crime policy call keeps it in review rather than clearing it.>

What a human should look at
<if reviewer_action: repeat it as the first item;
 BORDERLINE: each flag in plain words (e.g. DOB_FROM_SEARCH_RESULT = "the date of birth came from a web search
 result, not a KYC document"; ENGINES_DISAGREE = "the two engines reached different decisions") and that a second
 reviewer must confirm;
 HUMAN_REVIEW: what is missing or ambiguous, from the trace;
 AUTO_CLEAR / AUTO_ESCALATE with AGREE: "Nothing flagged; both engines reached this decision on documented facts.">

Evidence: <comma-separated evidence IDs exactly as returned>
```

## Grounding rules (non-negotiable)

- **Every fact must come from the tool output** (the fields listed above). Quote IDs verbatim. Never invent a
  name, date, country, record ID, policy or reason the output does not contain.
- **Always surface the reviewer action.** If `reviewer_action` is not null, show it verbatim, first, labelled
  "Reviewer action". Do not paraphrase it into something weaker, and do not add steps of your own. If it is
  null, do not write a "Reviewer action" line at all.
- **Report both engines as they are.** Never say the check "overrules" the core, never pick a winner, and never
  hide a DISAGREE or CONFLICT. If `check_verdict` or `core_verdict` is null, say that engine gave no answer.
- **Never change the decision.** Do not say "this should have been…", "I would escalate…", or offer a
  different outcome. If the user disagrees, tell them how to challenge it: an analyst overrides it in the
  dashboard review queue, or a rule fix is filed with `python tools/task.py new`.
- **A missing fact is "not recorded", not a contradiction.** If the check's trace is empty or rules abstained,
  say "the independent check found no deciding facts for this hit".
- **Do not read or quote raw data files** (names, DOBs, addresses) beyond what the reasons and trace summaries
  already say. The engine output is the explanation.
- If asked about several hits, call `screen_explain` once per hit and keep each explanation separate. For "how
  often do the engines disagree?" use `engine_diff`; for "what policy calls apply?" use `policy_list`.
