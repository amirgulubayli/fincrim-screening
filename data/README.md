# data/ (never committed)

The brief forbids committing `dev/`, and the repo goes public for submission. Everything here except this file is gitignored.

Put the data-room files in `data/dev/`: `POLICY.md`, `alerts.jsonl`, `hits.jsonl`, `customers.jsonl`, `accounts.jsonl`, `onboarding.jsonl`, `kyc_checks.jsonl`, `notes.jsonl`, `search_results.jsonl`, `name_variants.json`, `practice_labels.jsonl`.

If you only have the workbook, use the stopgap converter (it cuts long texts at 32k characters):

    uv run --with openpyxl python tools/import_xlsx.py "path/to/Screening Dev Set - browse.xlsx"
