"""Product Performance Automation (PPA) - monthly automation configuration.

SCHEDULE
  Original requirement (Product_Performance_Automation.pdf, Step 10): 2nd of every month at 06:00.
  Operational schedule (requested by the project owner, 2026-09-30): 3rd of every month at 06:00.
  Reason: operational schedule requested by project owner.
  The run always processes the previous full calendar month, computed from the run date.
"""
from pathlib import Path

PROJECT_CODE = "PPA"
BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
BUILD_SCRIPT = SCRIPTS / "build_report.py"
PUBLISH_SCRIPT = SCRIPTS / "publish_ph_task.py"
TEMPLATE = BASE / "template" / "report_template.html"
RAW_DIR = BASE / "raw"
OUTPUT_DIR = BASE / "output"
HISTORY_DIR = BASE / "14_History"            # one folder per reporting month, one sub-folder per run (never overwritten)
HISTORY_MASTER = HISTORY_DIR / "History_master.csv"   # append-only monthly history (the "History" tab)
LOG_DIR = BASE / "logs"
RUN_LOG = LOG_DIR / "run_log.csv"
RAW_MANIFEST = LOG_DIR / "raw_manifest.json"  # md5 of every stored raw file, to prove previous months never change

SCHEDULE = dict(task_name="PPA_Monthly_Product_Performance", day_of_month=3, time="06:00",
                original_requirement="2nd of every month at 06:00",
                reason="Operational schedule requested by project owner")

DB_ENV = "DATABASE_URL"            # order_management_copy: sales, master, listing_data, ph_task publish
LEDSONE_ENV = "WLP_SOURCE_DB_URL"   # ledsone: product_pk pack codes, PH master

EXPECTED_PHS = 30
MARKETPLACES = ["Amazon", "eBay", "B&Q"]
RAW_FILES = {"Amazon": "Raw_Amazon.csv", "eBay": "Raw_eBay.csv", "B&Q": "Raw_BQ.csv"}

# Google Sheet output (Step 7). The only Google credential in this workspace is the Windows Task Monitor's
# service account (Sheets scope). A Product Performance spreadsheet must be created by the owner and shared
# (Editor) with that account's client_email; then set SPREADSHEET_ID. Until then the same 7 tabs are written
# to an .xlsx workbook every month and the Sheet step reports NOT CONFIGURED (it never fakes success).
GOOGLE_SHEET = dict(
    spreadsheet_id="",   # <- set once the owner shares the spreadsheet
    credentials_file=r"C:\Users\LED 222\Automation\WindowsTaskMonitor\credentials\service-account.json",
)
SHEET_TABS = ["Amazon", "eBay", "B&Q", "Single SKU Units", "Combo Components", "Exceptions", "History"]

# Delivery (Step 10): the approved team channel is the PH Dashboard (tech_team_outputs.ph_task, team ph_priors),
# https://sales.vintageinterior.co.uk/ph-dashboard/ph-priors . No email/SMTP mechanism exists in this workspace.
DASHBOARD_URL = "https://sales.vintageinterior.co.uk/ph-dashboard/ph-priors"
EMAIL = dict(enabled=False, reason="No approved email/SMTP channel exists in this workspace; team access is via the PH Dashboard")
