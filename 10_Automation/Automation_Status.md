# Automation Status

| Item | Status |
|---|---|
| Production code | `automation/` (run.py, config.py, validate_output.py, outputs.py, scheduler.ps1, test_automation.py, README.md) — unchanged by the folder reorganisation |
| Business logic | `scripts/build_report.py`; publisher `scripts/publish_ph_task.py`; template `template/report_template.html` |
| Scheduler | `PPA_Monthly_Product_Performance` — 3rd of every month at 06:00 (Sri Lanka Standard Time); next run 2026-10-03 06:00 → processes 2026-09 |
| Run logs / alerts | Written by the automation to `logs/run_log.csv` and `logs/ALERT_*.txt` (paths used by the code; `10_Automation/Run_Logs` and `Alerts` are for copies/evidence) |
| Re-run | `python automation\run.py --month YYYY-MM [--dry-run]` — tested PASS |
| PH publishing | 30 PH Dashboard rows (project PPA) updated in place on PASS runs only |
| Delivery | PH Dashboard. Email: OPEN — not configured (no email channel exists) |
| Overall | **REVIEW** |
