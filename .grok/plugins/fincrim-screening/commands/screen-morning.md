---
description: Run the screening morning health check (eval + 5-run repro + review queue), write out/morning/YYYY-MM-DD.md and post the "Morning run YYYY-MM-DD" issue
argument-hint: "[data folder, default data/dev]"
---

Use the `morning-run` skill now, with data folder: $ARGUMENTS (use `data/dev` if empty).

Follow its hard rules: never edit, commit or push code; report the engine's numbers exactly; no data rows
in the issue. Finish with the issue URL, decision score, unsafe clearances, byte-identical yes/no and any
regression issue URLs.
