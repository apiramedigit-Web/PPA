# Product Performance Automation (PPA) — monthly automation

Source of truth: `Product_Performance_Automation.pdf`. Business logic lives in the approved `scripts/build_report.py`
(unchanged by this automation); publishing uses `scripts/publish_ph_task.py`.

## Schedule

| | |
|---|---|
| **Original requirement** (PDF Step 10) | 2nd of every month at 06:00 |
| **Operational schedule (in use)** | **3rd of every month at 06:00** (Sri Lanka Standard Time) |
| Reason | Operational schedule requested by project owner (2026-09-30) |
| Reporting period | Previous full calendar month, computed from the run date (never hard-coded; partial months are refused) |
| Windows task | `PPA_Monthly_Product_Performance` — `python automation\run.py --trigger scheduler`, InteractiveToken / LeastPrivilege (runs while the user is logged on; missed runs start when available) |

Examples: 2026-10-03 → Sep-2026 · 2026-11-03 → Oct-2026 · 2026-12-03 → Nov-2026 · 2027-01-03 → Dec-2026.
Manage the task: `automation\scheduler.ps1 [-Mode register|status|remove]`.

## Monthly workflow (Common Automation Workflow)

1. **Configuration / preflight** — `DATABASE_URL` and `WLP_SOURCE_DB_URL` present, month is complete, no other run active.
2. **Fetch** — Amazon, eBay, B&Q order lines (`order_management_copy.public.order_transaction`, Completed, UK) for the month.
   Stored once in `raw/<YYYY-MM>/Raw_Amazon.csv`, `Raw_eBay.csv`, `Raw_BQ.csv` (IDs as text). **Never overwritten**; re-runs read them.
   Empty/failed pulls are retried twice; an empty month is never saved and becomes an exception + REVIEW.
3. **Validate source / clean** — trim, ASIN uppercase, long eBay IDs kept as text, exact duplicates removed and listed.
4. **Mapping** — SKU on the order line → product master (`inv_products` + `inv_product_combo`, pack codes decoded via
   ledsone `inventory.product_pk`); unresolved lines → Marketplace + ID lookup in `listing_data` (UK, `wrong_sku = 0`).
   Unmatched → excluded + exception; incomplete / conflicting → excluded + exception (never guessed).
5. **Reports 1–3** — SKU exactly as sold, `Quantity Sold = SUM(quantity)`, `Sales Amount = SUM(order_total)` (never divided
   or converted), Avg Price, MoM % = (Current − Previous) ÷ Previous × 100, ±50% movement highlighted.
6. **Report 4 (units only)** — Single = qty; Pack = qty × pack size; Combo = qty × component qty per component
   ("+" combos split; coded combos via the combo components table). Avg Daily Units = Total ÷ days in month.
7. **Exceptions** — ID not in master, missing SKU / SKU type, unknown pack suffix, combo without components (kept in 1–3,
   out of 4), conflicting mapping, missing/zero marketplace data, reconciliation failure, ±50% MoM, plus PH checks.
8. **Reconciliation** — Raw − Exceptions = Report (qty and sales, per marketplace); Report 4 = singles + packs + combos;
   PH + (No PH) = Amazon totals; category totals = Report 4.
9. **Validate output** — 15 checks (raw loaded, IDs clean, mapping complete, reports built, sales unconverted, pack and
   combo conversion vs audit trails, Report 4 units only, exceptions, MoM recomputed, reconciliation, history appended,
   previous months unchanged, dashboard contains the month, PH filtering).
10. **Store output** — History appended (append-only), workbook written, Google Sheet updated if configured.
11. **Publish** — only a PASS run: `publish_ph_task.py` updates the 30 PH Dashboard rows in place
    (`tech_team_outputs.ph_task`, project PPA, team ph_priors). A re-run of an older month never replaces a newer published month.
12. **Archive + log** — see below. Exit code 0 PASS, 1 REVIEW, 2 FAILED (picked up by the Windows Task Monitor).

## Outputs

| Output | Location |
|---|---|
| Dashboard (standalone HTML, Month / PH / Product Category filters) | `output/Product_Performance_<YYYY-MM>.html` (+ `data_<YYYY-MM>.json`) |
| Google-Sheet tabs as a workbook: Amazon, eBay, B&Q, Single SKU Units, Combo Components, Exceptions, History (filters on every tab) | `output/Product_Performance_<YYYY-MM>.xlsx` |
| History (append-only, one block per month, never rewritten) | `14_History/History_master.csv` |
| Monthly archive: raw copy, cleaned lines, report CSVs, exceptions, reconciliation, dashboard, workbook, run log | `14_History/<YYYY-MM>/run_<timestamp>/` (new folder every run) |
| Run log (one row per run: marketplace / mapping / report / exception / reconciliation / history / sheet / dashboard / delivery / overall status) | `logs/run_log.csv` |
| Failure / REVIEW alert (Error, Root Cause, Impact, Month, Marketplace, Records, Proposed Fix, Validation Required) | `logs/ALERT_<YYYY-MM>_<run>.txt` |
| Raw integrity manifest (md5 of every stored raw file) | `logs/raw_manifest.json` |

## Google Sheet

`automation/outputs.py → push_google_sheet` appends the month to each tab (months already present are skipped) using the
workspace's existing service account (`WindowsTaskMonitor/credentials/service-account.json`, Sheets scope).
**Not active yet:** no Product Performance spreadsheet exists. The owner must create it, share it (Editor) with the service
account's client email, and set `GOOGLE_SHEET["spreadsheet_id"]` in `config.py`. Until then the run log shows
`NOT CONFIGURED` and the identical 7 tabs are delivered as the monthly `.xlsx`.

## Delivery / email

The approved team channel is the PH Dashboard: https://sales.vintageinterior.co.uk/ph-dashboard/ph-priors (30 PHs).
No email/SMTP mechanism exists in this workspace, so no email is sent; failures are reported through the exit code
(Windows Task Monitor), `logs/run_log.csv` and the ALERT file.

## Re-run

```
python automation\run.py --month 2026-09            # re-run a completed month from its stored raw data
python automation\run.py --month 2026-09 --dry-run  # same, without publishing
python automation\run.py --as-of 2026-10-03 --dates-only
```
A re-run uses the stored raw files, rebuilds everything, and adds a new archive folder. History for a month already
stored is not rewritten: identical → noted; different → flagged (REVIEW) and not applied.

## Failure handling

Any failing stage stops delivery: nothing is published, the previous validated dashboard stays live, and the run log +
ALERT file record stage, error, root cause, impact, month, marketplace, records, proposed fix and required validation.
Business rules are never changed to make a check pass.

## Tests

`python automation\test_automation.py` — offline: month resolution, all output checks on the validated build, detection
of reconciliation failure / divided sales / wrong MoM / sales in Report 4 / missing marketplace data, append-only history.
