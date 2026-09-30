# Google Sheet Status

**Status: OPEN — not configured.**

- Required tabs (PDF Step 10): Amazon, eBay, B&Q, Single SKU Units, Combo Components, Exceptions, History.
- Code ready: `automation/outputs.py → push_google_sheet` (appends each month; months already present are skipped).
- Blocker: no Product Performance spreadsheet exists. The owner must create it, share it (Editor) with the service account in `C:\Users\LED 222\Automation\WindowsTaskMonitor\credentials\service-account.json`, and set `GOOGLE_SHEET["spreadsheet_id"]` in `automation/config.py`.
- Until then each run writes the same 7 tabs to `output/Product_Performance_<YYYY-MM>.xlsx`; History is kept in `14_History/History_master.csv` (append-only).
