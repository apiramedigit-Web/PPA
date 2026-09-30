"""Step 9 validation of a built month. Pure checks on the build output - nothing is changed here."""
import csv, hashlib, json, re
from decimal import Decimal

import config

MOVE = 50


def pct(cur, prev):
    if prev in (None, 0):
        return None
    return float(((Decimal(str(cur)) - Decimal(str(prev))) / Decimal(str(prev)) * 100).quantize(Decimal("0.1")))


def raw_md5s():
    return {str(p.relative_to(config.BASE)).replace("\\", "/"): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(config.RAW_DIR.glob("*/*.csv"))}


def check_raw_unchanged(month):
    """Every raw file already in the manifest must be byte-identical; new files are added for this month only."""
    now = raw_md5s()
    old = json.loads(config.RAW_MANIFEST.read_text(encoding="utf-8")) if config.RAW_MANIFEST.exists() else {}
    changed = [f for f, h in old.items() if now.get(f) != h]
    new = [f for f in now if f not in old]
    return changed, new, now


def validate(month, data, html):
    """Returns list of (no, name, ok, detail)."""
    m = data["meta"]
    months = m["months"]
    res = []
    add = lambda n, name, ok, det="": res.append((n, name, bool(ok), det))

    info = m["raw_info"].get(month, {})
    rc = {(r["month"], r["mp"]): r for r in data["recon13"]}
    add(1, "Raw data loaded (all 3 marketplaces, rows > 0)",
        all(info.get(mp, {}).get("file") and rc[(month, mp)]["raw_rows"] > 0 for mp in config.MARKETPLACES),
        ", ".join(f"{mp} {rc[(month, mp)]['raw_rows']} lines ({info.get(mp, {}).get('origin')})" for mp in config.MARKETPLACES))

    ids = [e["id"] for e in data["exc"] if e["mp"] == "Amazon" and e["id"] and e["type"] != "Unusual movement (>±50% MoM)"]
    ids += [a for r in data["r13"] if r[1] == "Amazon" for a in r[14].replace(" ...", "").split(" | ") if a]
    ebay_ids = [e["id"] for e in data["exc"] if e["mp"] == "eBay" and e["id"]] + [a for r in data["r13"] if r[1] == "eBay" for a in r[14].replace(" ...", "").split(" | ") if a]
    bad_amz = [i for i in ids if i != i.strip().upper()]
    bad_sci = [i for i in ebay_ids if re.search(r"[eE]\+|\.", i)]
    add(2, "IDs cleaned (ASIN trimmed/uppercase; eBay IDs kept as text, no scientific notation)", not bad_amz and not bad_sci,
        f"bad ASIN {len(bad_amz)}, bad eBay IDs {len(bad_sci)}")

    add(3, "Mapping completed (every raw line is in a report or an exception)",
        all(rc[(month, mp)]["diff_qty"] == 0 for mp in config.MARKETPLACES), "raw - exceptions - report = 0 for each marketplace")

    add(4, "Reports 1-3 generated", all(any(r[0] == month and r[1] == mp for r in data["r13"]) for mp in config.MARKETPLACES),
        ", ".join(f"{mp} {sum(1 for r in data['r13'] if r[0] == month and r[1] == mp)} rows" for mp in config.MARKETPLACES))

    sales_ok = all(abs(sum(r[6] for r in data["r13"] if r[0] == month and r[1] == mp) - (rc[(month, mp)]["raw_sales"] - rc[(month, mp)]["exc_sales"])) < 0.005
                   for mp in config.MARKETPLACES)
    add(5, "Sales Amount unchanged / not converted (report sales = raw line sales - exception sales)", sales_ok)

    r4c = {(r["month"], r["mp"]): r for r in data["recon4"]}
    pk = {mp: sum(x[5] for x in data["pack"] if x[0] == month and x[1] == mp) for mp in config.MARKETPLACES}
    cb = {mp: sum(x[5] for x in data["combo"] if x[0] == month and x[1] == mp) for mp in config.MARKETPLACES}
    add(6, "Pack conversion (Qty x Pack Size) matches the pack audit trail",
        all(pk[mp] == r4c[(month, mp)]["pack_units"] for mp in config.MARKETPLACES), str(pk))
    add(7, "Combo conversion (Combo Qty x Component Qty) matches the combo audit trail",
        all(cb[mp] == r4c[(month, mp)]["combo_units"] for mp in config.MARKETPLACES), str(cb))

    r4m = [r for r in data["r4"] if r[0] == month]
    add(8, "Report 4 contains units only (no sales fields)", r4m and all(len(r) == 9 and all(isinstance(v, int) for v in r[2:6]) for r in r4m),
        f"{len(r4m)} single SKUs")

    types = {e["type"] for e in data["exc"] if e["month"] == month}
    add(9, "Exceptions generated", isinstance(data["exc"], list), f"{sum(1 for e in data['exc'] if e['month'] == month)} rows; types: {sorted(types)}")

    wrong = [r[2] for r in data["r13"] if r[0] == month and (r[10] != pct(r[5], r[8]) or r[11] != pct(r[6], r[9]))]
    add(10, "MoM calculations correct (recomputed for every Report 1-3 row)", not wrong, f"mismatches {len(wrong)}")

    recs = [r for k in ("recon13", "recon4", "recon_ph", "recon_cat") for r in data.get(k, [])]
    add(11, "Reconciliation passes (Reports 1-3, Report 4, PH, category)", all(r["status"] == "PASS" for r in recs) and m["status"] == "PASS",
        f"{sum(r['status'] == 'PASS' for r in recs)}/{len(recs)} PASS")

    add(14, "Dashboard contains the new month", m["report_month"] == month and m["month_labels"][month] in html and month in months,
        f"report month {m['report_month']}, window {months}")
    add(15, "PH filtering correct (30 authoritative PHs, PH + (No PH) = Amazon totals)",
        len(m["ph"]["phs"]) == config.EXPECTED_PHS and all(r["status"] == "PASS" for r in data["recon_ph"]) and 'id="ph"' in html,
        f"{len(m['ph']['phs'])} PHs")
    return res


def history_checks(month, before_rows, after_rows):
    """12/13: this month appended; every other month's history rows byte-identical."""
    other_b = [r for r in before_rows if r["month"] != month]
    other_a = [r for r in after_rows if r["month"] != month]
    return [(12, "History appended for the month", any(r["month"] == month for r in after_rows),
             f"{sum(1 for r in after_rows if r['month'] == month)} rows for {month}"),
            (13, "Previous months unchanged (history rows)", json.dumps(other_b) == json.dumps(other_a),
             f"{len(other_a)} rows of other months")]


def read_history():
    if not config.HISTORY_MASTER.exists():
        return []
    with config.HISTORY_MASTER.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
