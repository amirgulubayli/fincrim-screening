---
description: Start the screening engine + review dashboard (CLI embedded) and share the URL
argument-hint: "[data folder or workbook path]"
---

Use the `launch-dashboard` skill. If an argument is given: a path ending in `.xlsx` is the browse
workbook (pass as `xlsx`), anything else is the data folder (pass as `data`). Then call `dashboard_up`,
wait for it to be ready, and reply with the URL plus the headline numbers (hits, tiers, unsafe
clearances, decision score). Remind the user the bottom panel is a live `screen` CLI terminal.

$ARGUMENTS
