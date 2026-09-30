# Project Architecture

Full operational documentation: `automation/README.md` (workflow, schedule, calculations, exceptions, reconciliation, outputs, re-run, failure handling).

## Flow
Scheduler (3rd, 06:00) → `automation/run.py` → preflight → `scripts/build_report.py` (fetch → clean → map → Reports 1–3 → conversion → Report 4 → exceptions → reconciliation → dashboard) → raw integrity → 15 output checks → History (append-only) + workbook (+ Google Sheet when configured) → `scripts/publish_ph_task.py` (PASS only) → archive `14_History/<month>/run_<ts>/` → `logs/run_log.csv` (+ ALERT).

## Paths used by the code (do not move)
`raw/<YYYY-MM>/Raw_*.csv` · `master/<date>/` · `output/` (dashboard, data JSON, workbook, `publish/ph_task_rows.json`) · `14_History/` · `logs/` · `scripts/` · `template/` · `automation/`.
The numbered folders `01_…15_` are documentation / evidence areas; production data stays in the paths above.

## Product Category (Report 4 column + filter)
Display only. Source: `order_transaction.category_name` (PH-portfolio names) from the SKU's own order lines, else from packs/combos containing it; `Special-*` ignored; several names → the one with most lines. The true product classification `inv_products.sub_category` has no name table in the database. Status: Pending (name table sync from OMS).
