"""Product Performance Automation (PPA) - monthly run.

  python run.py                         scheduled run: previous full calendar month (3rd of the month, 06:00)
  python run.py --month 2026-09         re-run / run a specific completed month (stored raw data is reused)
  python run.py --month 2026-08 --dry-run   everything except publishing (tests)
  python run.py --as-of 2026-10-03 --dates-only   show which month a run on that date would process

Common Automation Workflow:
  Scheduler -> Configuration -> Fetch Data -> Validate Source Data -> Process Business Logic -> Transform
  -> Generate Output -> Validate Output -> Publish / Store Output -> Archive -> Logging
The business logic is the approved scripts/build_report.py (unchanged); publishing is scripts/publish_ph_task.py.
Exit code: 0 PASS, 1 REVIEW, 2 FAILED (read by the Windows Task Monitor).
"""
import argparse, csv, datetime as dt, json, os, subprocess, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import outputs  # noqa: E402
import validate_output as vo  # noqa: E402

RUN_COLS = ["run_date", "run_id", "trigger", "reporting_month", "amazon_status", "ebay_status", "bq_status", "mapping_status",
            "report_status", "exception_status", "reconciliation_status", "history_status", "sheet_status", "dashboard_status",
            "delivery_status", "overall_status", "error", "timestamp"]


def previous_full_month(d):
    first = d.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return f"{last_prev.year:04d}-{last_prev.month:02d}"


def py(script, *args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(config.BASE))


def write_run_log(rec):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    first = not config.RUN_LOG.exists()
    with config.RUN_LOG.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_COLS, extrasaction="ignore")
        if first:
            w.writeheader()
        w.writerow(rec)


def write_alert(rec, detail):
    path = config.LOG_DIR / f"ALERT_{rec['reporting_month']}_{rec['run_id']}.txt"
    lines = [f"PRODUCT PERFORMANCE AUTOMATION - {rec['overall_status']} - NOT DISTRIBUTED AS A SUCCESSFUL REPORT",
             f"Timestamp: {rec['timestamp']}", f"Affected Month: {rec['reporting_month']}", f"Stage: {detail.get('stage')}",
             f"Error: {rec['error']}", f"Root Cause: {detail.get('root_cause')}", f"Impact: {detail.get('impact')}",
             f"Affected Marketplace: {detail.get('marketplace')}", "Affected Records:", *[f"  - {x}" for x in detail.get("records", [])],
             f"Proposed Fix: {detail.get('fix')}", f"Validation Required: {detail.get('validation')}"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month")
    ap.add_argument("--as-of")
    ap.add_argument("--dates-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="no publishing (tests)")
    ap.add_argument("--trigger", default="manual")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    month = a.month or previous_full_month(today)
    if a.dates_only:
        print(f"run date {today} -> reporting month {month}")
        return 0
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rec = {c: "" for c in RUN_COLS}
    rec.update(run_date=str(dt.date.today()), run_id=run_id, trigger=a.trigger + (" (dry-run)" if a.dry_run else ""), reporting_month=month)
    detail = {}
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock = config.LOG_DIR / "run.lock"
    try:
        # ---- configuration / preflight
        stage = "preflight"
        cur = f"{dt.date.today():%Y-%m}"
        missing = [e for e in (config.DB_ENV, config.LEDSONE_ENV) if not os.environ.get(e)]
        if missing:
            raise RuntimeError(f"environment variables not set: {missing}")
        if month >= cur:
            raise RuntimeError(f"{month} is not a completed month (today {dt.date.today()}) - partial months are never processed")
        if lock.exists():
            raise RuntimeError(f"another run is in progress (lock {lock})")
        lock.write_text(run_id)

        # ---- fetch + validate source + business logic + transform + generate (approved build, unchanged)
        stage = "build (fetch, clean, map, reports, conversion, exceptions, reconciliation, dashboard)"
        b = py(config.BUILD_SCRIPT, "--month", month)
        (config.LOG_DIR / f"build_{month}_{run_id}.log").write_text(b.stdout + "\n" + b.stderr, encoding="utf-8")
        if b.returncode != 0:
            raise RuntimeError(f"build_report.py exited {b.returncode}: {b.stderr.strip()[-400:]}")
        data = json.loads((config.OUTPUT_DIR / f"data_{month}.json").read_text(encoding="utf-8"))
        html_path = config.OUTPUT_DIR / f"Product_Performance_{month}.html"
        html = html_path.read_text(encoding="utf-8")

        # ---- raw data integrity (previous months' raw files must never change)
        stage = "raw integrity"
        changed, new, now = vo.check_raw_unchanged(month)
        if changed:
            detail.update(root_cause="stored raw files changed after they were first saved", records=changed, marketplace="see files",
                          impact="history no longer reproducible", fix="restore the raw files from 14_History", validation="md5 matches manifest")
            raise RuntimeError(f"stored raw files changed: {changed}")
        config.RAW_MANIFEST.write_text(json.dumps(now, indent=1, sort_keys=True), encoding="utf-8")

        # ---- validate output (Step 9)
        stage = "validate output"
        checks = vo.validate(month, data, html)
        rc = {(r["month"], r["mp"]): r for r in data["recon13"]}
        for mp, key in (("Amazon", "amazon_status"), ("eBay", "ebay_status"), ("B&Q", "bq_status")):
            r = rc[(month, mp)]
            rec[key] = "PASS" if r["raw_rows"] > 0 and r["status"] == "PASS" else ("FAILED - no data" if r["raw_rows"] == 0 else "REVIEW")
        ok = {n: o for n, _, o, _ in checks}
        rec["mapping_status"] = "PASS" if ok[3] else "REVIEW"
        rec["report_status"] = "PASS" if ok[4] and ok[5] and ok[8] and ok[10] else "REVIEW"
        rec["exception_status"] = "PASS" if ok[9] else "REVIEW"
        rec["reconciliation_status"] = "PASS" if ok[11] else "REVIEW"
        rec["dashboard_status"] = "BUILT" if ok[14] and ok[15] else "REVIEW"

        # ---- store output: history (append-only), workbook / Google Sheet
        stage = "history / sheet"
        before = vo.read_history()
        hist_msg, hist_same = outputs.append_history(month, data, run_id) if all(ok.values()) else ("NOT APPENDED (validation failed)", True)
        after = vo.read_history()
        checks += vo.history_checks(month, before, after) if all(ok.values()) else []
        rec["history_status"] = hist_msg
        wb = config.OUTPUT_DIR / f"Product_Performance_{month}.xlsx"
        tab_counts = outputs.write_workbook(wb, month, data)
        if all(c[2] for c in checks) and hist_same:
            try:
                s_status, s_detail = outputs.push_google_sheet(month, data)
            except Exception as e:  # reported, never hidden
                s_status, s_detail = "FAILED", f"{type(e).__name__}: {e}"
        else:
            s_status, s_detail = "NOT UPDATED", "validation did not pass"
        rec["sheet_status"] = f"{s_status} (workbook {wb.name} written)"

        failed = [c for c in checks if not c[2]]
        passed = not failed and hist_same
        # ---- publish (only a PASS run; never an older month over a newer published one)
        stage = "publish"
        reg = config.OUTPUT_DIR / "publish" / "ph_task_rows.json"
        pub_month = max((r.get("month", "") for r in json.loads(reg.read_text(encoding="utf-8")).values()), default="") if reg.exists() else ""
        if not passed:
            rec["delivery_status"] = "NOT DELIVERED (validation/reconciliation did not pass)"
        elif a.dry_run:
            rec["delivery_status"] = "SKIPPED (dry-run)"
        elif pub_month and month < pub_month:
            rec["delivery_status"] = f"SKIPPED (re-run of {month}; PH Dashboard already shows newer month {pub_month})"
        else:
            p = py(config.PUBLISH_SCRIPT, "--month", month)
            (config.LOG_DIR / f"publish_{month}_{run_id}.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
            if p.returncode != 0 or "all read-backs PASS: True" not in p.stdout:
                raise RuntimeError(f"publish failed (exit {p.returncode}): {(p.stdout + p.stderr).strip()[-400:]}")
            rec["delivery_status"] = f"PUBLISHED to PH Dashboard ({config.DASHBOARD_URL}); email not configured"
            rec["dashboard_status"] = "PUBLISHED"

        rec["overall_status"] = "PASS" if passed else "REVIEW"
        if not passed:
            recs = [f"check {n} {name}: {det}" for n, name, _, det in failed]
            recs += [f"{r['month']} {r.get('mp', '')} {k}: {json.dumps(r)[:300]}" for k in ("recon13", "recon4", "recon_ph", "recon_cat")
                     for r in data.get(k, []) if r["status"] != "PASS"]
            if not hist_same:
                recs.append(f"history for {month} already stored and differs from this run - not overwritten")
            detail.update(stage="validate output", root_cause="; ".join(f"{name}" for _, name, _, _ in failed) or "history mismatch",
                          impact="report not distributed; PH Dashboard keeps the previous validated version",
                          marketplace=", ".join(mp for mp, k in (("Amazon", "amazon_status"), ("eBay", "ebay_status"), ("B&Q", "bq_status")) if rec[k] != "PASS") or "all",
                          records=recs, fix="investigate the listed checks/records; do not change business rules to force a pass",
                          validation="re-run python run.py --month " + month + " and confirm all 15 checks PASS")
            rec["error"] = f"{len(failed)} validation check(s) failed" + ("; history mismatch" if not hist_same else "")
        rec["timestamp"] = dt.datetime.now().isoformat(timespec="seconds")
        log_record = dict(rec, checks=[dict(no=n, check=name, ok=o, detail=det) for n, name, o, det in sorted(checks)],
                          history=hist_msg, sheet=s_detail, workbook_tabs=tab_counts, schedule=config.SCHEDULE)
        arch = outputs.archive(month, run_id, data, html_path, wb, log_record)
        rec_note = f"archived to {arch.relative_to(config.BASE)}"
    except Exception as e:
        rec["overall_status"] = "FAILED"
        rec["error"] = f"[{stage}] {type(e).__name__}: {e}"
        rec["timestamp"] = dt.datetime.now().isoformat(timespec="seconds")
        detail.setdefault("stage", stage)
        detail.setdefault("root_cause", str(e))
        detail.setdefault("impact", "no report delivered for this month; previous validated dashboard stays live")
        detail.setdefault("marketplace", "all")
        detail.setdefault("records", [traceback.format_exc().splitlines()[-1]])
        detail.setdefault("fix", "fix the cause above and re-run: python run.py --month " + month)
        detail.setdefault("validation", "all 15 checks PASS and reconciliation PASS")
        rec_note = ""
    finally:
        if lock.exists() and lock.read_text() == run_id:
            lock.unlink()
    write_run_log(rec)
    if rec["overall_status"] != "PASS":
        print("ALERT written:", write_alert(rec, detail))
    print(json.dumps(rec, indent=1))
    if rec_note:
        print(rec_note)
    return {"PASS": 0, "REVIEW": 1}.get(rec["overall_status"], 2)


if __name__ == "__main__":
    sys.exit(main())
