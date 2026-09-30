# Test Results (verified 2026-09-30)

Evidence copied from `14_History/<month>/run_*/` into `07_Validation/<Month>_2026/` (run_log + reconciliation).

| Month | Amazon | eBay | B&Q | Output checks | Reconciliation | History |
|---|---|---|---|---|---|---|
| June 2026 | PASS | PASS | PASS | 15/15 PASS | PASS | 6,259 rows appended |
| July 2026 | PASS | PASS | PASS | 15/15 PASS | PASS | 6,147 rows appended |
| August 2026 | PASS | PASS | PASS | 15/15 PASS | PASS (24/24) | 6,462 rows appended |

- **Re-run:** PASS — Aug-2026 rebuilt from stored raw data identical to the validated/published build; history "ALREADY PRESENT – identical"; overlapping months identical across builds.
- **Failure-path test:** PASS — `--month 2026-09` (incomplete) → FAILED in preflight, ALERT file written, exit code 2, nothing published.
- **Offline tests** (`automation/test_automation.py`): 13/13 PASS.
- **PH Dashboard:** August 2026 validated version live for all 30 PHs (ph_task ids 1974–2003, html md5 4c247a05…).
- **Scheduler:** `PPA_Monthly_Product_Performance`, 3rd of every month at 06:00.

**Current overall status: REVIEW** (open delivery items — see `15_Closure/Closure_Status.md`).
