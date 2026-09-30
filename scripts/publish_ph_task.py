"""Publish the validated Product Performance dashboard to the PH Dashboard (tech_team_outputs.ph_task).

Same mechanism as the other PH projects (e.g. ANPIA): one ph_task row per PH, team 'ph_priors', one shared
dashboard whose PH filter selects the PH's Amazon data. First run registers the rows (INSERT); later runs update
them in place (never DELETE). Every write is guarded by id + project_code + assigned_user + assigned_user_team.

Usage:
  python publish_ph_task.py --month 2026-08 --only Dilakshiga   # single-PH test
  python publish_ph_task.py --month 2026-08                     # all PHs in the report's PH master
"""
import argparse, datetime as dt, hashlib, json, os, re, sys
from pathlib import Path

import psycopg

BASE = Path(__file__).resolve().parent.parent
PUB = BASE / "output" / "publish"
REGISTRY = PUB / "ph_task_rows.json"
TABLE = "tech_team_outputs.ph_task"
PROJECT = dict(project_code="PPA",
               project_name="Product Performance Automation - Monthly Sales Reports by Marketplace + Single SKU Units",
               task_name="Product Performance Report — Amazon / eBay / B&Q (SKU as sold) + Single SKU Units",
               team="Technical", team_ph="ph_priors", developer="Apirame")


def load_source(month):
    path = BASE / "output" / f"Product_Performance_{month}.html"
    html = path.read_text(encoding="utf-8")
    data = json.loads(re.search(r"const D=(\{.*?\});\r?\n", html, re.S).group(1).replace("<\\/", "</"))
    meta = data["meta"]
    checks = {
        "report month matches": meta["report_month"] == month,
        "overall status PASS": meta["status"] == "PASS",
        "all reconciliations PASS": all(r["status"] == "PASS" for k in ("recon13", "recon4", "recon_ph", "recon_cat") for r in data.get(k, [])),
        "30 PHs in data": len(meta["ph"]["phs"]) == 30,
        "PH filter present": 'id="ph"' in html,
        "Product Category filter present": 'id="cat"' in html,
    }
    if not all(checks.values()):
        sys.exit(f"STOP - source dashboard failed validation: {checks}")
    return path, html, meta, checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--only", help="publish to this one PH only (test)")
    args = ap.parse_args()
    path, html, meta, checks = load_source(args.month)
    md5 = hashlib.md5(html.encode("utf-8")).hexdigest()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    phs = meta["ph"]["phs"] if not args.only else [args.only]
    if args.only and args.only not in meta["ph"]["phs"]:
        sys.exit(f"{args.only} is not in the PH master")
    y, m = map(int, args.month.split("-"))
    label = dt.date(y, m, 1).strftime("%b-%Y")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {}
    last = max(registry.values(), key=lambda r: r.get("version_level", 0), default=None)
    version = 1 if last is None else (last["version_level"] if last.get("month") == args.month else last["version_level"] + 1)
    task_id = f"PPA_{args.month}_V{version:03d}"
    desc = (f"Report month {label}. Product Performance Automation: Reports 1-3 (Amazon, eBay, B&Q - SKU as sold, qty, sales, MoM) "
            f"+ Report 4 Single SKU Units (packs/combos converted). Filters: Month, PH (30 PHs, Amazon ASIN allocation; eBay/B&Q not PH-attributed), "
            f"Product Category. Reconciliation PASS. build={md5[:12]} generated {meta['generated']}.")
    PUB.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        before = c.execute(f"SELECT count(*) FROM {TABLE} WHERE project_code=%s", (PROJECT["project_code"],)).fetchone()[0]
        # previous published versions of our rows (for history)
        known = {p: registry[p]["id"] for p in phs if p in registry}
        if known:
            prev = c.execute(f"SELECT id, assigned_user, md5(html_content), html_content FROM {TABLE} WHERE id = ANY(%s)", (list(known.values()),)).fetchall()
            seen = set()
            for rid, user, pm, ph_html in prev:
                if pm not in seen and pm != md5:
                    (PUB / f"previous_published_{stamp}_{pm[:8]}.html").write_text(ph_html, encoding="utf-8"); seen.add(pm)
        results = []
        with c.cursor() as cur:
            for ph in phs:
                if ph in registry:
                    rid = registry[ph]["id"]
                    cur.execute(f"""UPDATE {TABLE} SET html_content=%s, description=%s, task_id=%s, version_level=%s, version_status='released', updated_at=now()
                                    WHERE id=%s AND project_code=%s AND assigned_user=%s AND assigned_user_team=%s""",
                                (html, desc, task_id, version, rid, PROJECT["project_code"], ph, PROJECT["team_ph"]))
                    if cur.rowcount != 1:
                        c.rollback(); sys.exit(f"STOP - update of row {rid} ({ph}) touched {cur.rowcount} rows; nothing committed")
                    action = "updated"
                else:
                    dup = cur.execute(f"SELECT id FROM {TABLE} WHERE project_code=%s AND assigned_user=%s AND assigned_user_team=%s",
                                      (PROJECT["project_code"], ph, PROJECT["team_ph"])).fetchall()
                    if dup:
                        c.rollback(); sys.exit(f"STOP - {ph} already has PPA row(s) {dup} not in the registry; nothing committed")
                    rid = cur.execute(f"""INSERT INTO {TABLE} (id, project_name, project_code, task_name, task_id, team, developer, assigned_user,
                                           html_content, description, phase_level, version_level, version_status, assigned_user_team, created_at, updated_at)
                                          VALUES (nextval('tech_team_outputs.ph_task_id_seq'), %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, 'released', %s, now(), now())
                                          RETURNING id""",
                                      (PROJECT["project_name"], PROJECT["project_code"], PROJECT["task_name"], task_id, PROJECT["team"], PROJECT["developer"], ph,
                                       html, desc, version, PROJECT["team_ph"])).fetchone()[0]
                    action = "inserted"
                results.append((ph, rid, action))
            ids = [r[1] for r in results]
            ok = cur.execute(f"SELECT count(*) FROM {TABLE} WHERE id = ANY(%s) AND md5(html_content)=%s AND project_code=%s AND assigned_user_team=%s",
                             (ids, md5, PROJECT["project_code"], PROJECT["team_ph"])).fetchone()[0]
            after = cur.execute(f"SELECT count(*) FROM {TABLE} WHERE project_code=%s", (PROJECT["project_code"],)).fetchone()[0]
            dups = cur.execute(f"SELECT assigned_user FROM {TABLE} WHERE project_code=%s GROUP BY 1 HAVING count(*)>1", (PROJECT["project_code"],)).fetchall()
            inserted = sum(1 for r in results if r[2] == "inserted")
            if ok != len(ids) or after != before + inserted or dups:
                c.rollback(); sys.exit(f"STOP - pre-commit check failed: {ok}/{len(ids)} rows carry the html, PPA rows {before}->{after} "
                                       f"(expected +{inserted}), duplicates {dups}; nothing committed")
        c.commit()

    for ph, rid, _ in results:
        registry[ph] = dict(id=rid, team=PROJECT["team_ph"], version_level=version, task_id=task_id, month=args.month)
    REGISTRY.write_text(json.dumps(registry, indent=1, sort_keys=True), encoding="utf-8")
    # independent read-back after commit
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("SET default_transaction_read_only = on")
        back = {r[0]: r for r in c.execute(f"""SELECT id, assigned_user, assigned_user_team, project_code, task_id, version_status, md5(html_content)=%s, length(html_content)
                                                FROM {TABLE} WHERE id = ANY(%s)""", (md5, ids))}
    log = dict(published_at=dt.datetime.now().isoformat(timespec="seconds"), month=args.month, source=str(path), source_sha256=sha,
               html_md5=md5, bytes=len(html.encode("utf-8")), task_id=task_id, version=version, checks=checks,
               rows=[dict(ph=ph, id=rid, action=a, readback_ok=bool(back.get(rid) and back[rid][1] == ph and back[rid][6])) for ph, rid, a in results])
    (PUB / f"publish_log_{stamp}.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in log.items() if k != "rows"}, indent=1))
    for r in log["rows"]:
        print(f"  {r['ph']:18} id {r['id']:5} {r['action']:8} read-back {'PASS' if r['readback_ok'] else 'FAIL'}")
    print("PPA rows now:", len(registry), "| all read-backs PASS:", all(r["readback_ok"] for r in log["rows"]))


if __name__ == "__main__":
    main()
