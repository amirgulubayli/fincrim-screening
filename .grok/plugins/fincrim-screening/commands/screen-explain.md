---
description: Explain why the screening engine decided a hit the way it did, for a compliance analyst, grounded in the rule trace
argument-hint: "<HIT_ID>"
---

Use the `explain-hit` skill for hit: $ARGUMENTS

Call `screen_explain` for that hit and narrate only what it returns: the decision core's verdict, the
independent check's verdict and facet trace, whether they agree, and - always, verbatim - the reviewer action
when there is one. The engine's decision is final; do not suggest a different one.
