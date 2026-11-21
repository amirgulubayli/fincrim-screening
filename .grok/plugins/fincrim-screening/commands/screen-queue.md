---
description: Show the screening review queue for one tier (HUMAN_REVIEW by default, or BORDERLINE / AUTO_ESCALATE / AUTO_CLEAR)
argument-hint: "[tier] [limit]"
---

Call the MCP tool `review_queue` with tier and limit parsed from: $ARGUMENTS (defaults: `HUMAN_REVIEW`, 20).

Show the result as a table: hit_id, decision, reason_code, flags. Under it, one sentence on what the tier
means (HUMAN_REVIEW = analyst decides; BORDERLINE = automated but rests on a weak fact, second reviewer
confirms). Report the engine's output as-is; do not re-decide any hit.
