"""Product Performance Automation - monthly build.

Source of truth: Product_Performance_Automation.pdf (Reports 1-4, Exceptions, Reconciliation).

Usage:  python build_report.py [--month YYYY-MM]
Default month = previous full calendar month. The report month plus the two
prior months are built (3-month test window); one further month is pulled as
the MoM baseline for the earliest month.

Data sources (discovered 2026-09-30):
  sales   order_management_copy.public.order_transaction   (DATABASE_URL)
  master  order_management_copy.public.inv_products + inv_product_combo
  packs   ledsone.inventory.product_pk                    (WLP_SOURCE_DB_URL)
Raw monthly extracts are stored once in raw/<YYYY-MM>/ and never overwritten;
re-runs read the stored files.
"""
import argparse, calendar, csv, datetime as dt, json, os, re, shutil, sys, time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

BASE = Path(__file__).resolve().parent.parent
RAW_DIR, MASTER_DIR, OUT_DIR = BASE / "raw", BASE / "master", BASE / "output"
TEMPLATE = BASE / "template" / "report_template.html"

MARKETPLACES = [("AMAZON", "Amazon", "Raw_Amazon.csv"), ("EBAY", "eBay", "Raw_eBay.csv"), ("B&Q", "B&Q", "Raw_BQ.csv")]
RAW_COLS = ["order_item_info", "order_id", "source_name", "asin", "item_id", "sku", "quantity",
            "order_total", "item_price", "order_date", "ss_name", "fba_sales"]
DUP_KEY = ["order_id", "asin", "item_id", "sku", "quantity", "order_total", "item_price", "order_date", "ss_name"]
MOVE_PCT = Decimal(50)
Q2 = Decimal("0.01")

RAW_SQL = """
SELECT order_item_info::text, order_id::text, source_name, asin::text, item_id::text, sku::text,
       COALESCE(quantity,0)::text, COALESCE(order_total,0)::numeric(14,2)::text, item_price::text,
       order_date::text, ss_name, fba_sales::text
FROM public.order_transaction
WHERE source_name = %s AND order_status = 'Completed' AND market_place = 'UK'
  AND order_date >= %s AND order_date < %s
ORDER BY order_item_info"""

SCOPE_SQL = """
SELECT source_name, (market_place = 'UK') AS is_uk, order_status, count(*),
       COALESCE(sum(quantity),0), COALESCE(sum(order_total),0)::numeric(14,2)
FROM public.order_transaction
WHERE source_name IN ('AMAZON','EBAY','B&Q') AND order_date >= %s AND order_date < %s
GROUP BY 1,2,3"""

# Marketplace + ID -> SKU master. B&Q is absent: its listing_data ref_id is the EAN barcode,
# which order_transaction does not carry for B&Q lines.
LISTING_CHANNEL = {"Amazon": 1, "eBay": 2}
LISTING_SQL = """
SELECT which_channel, upper(trim(ref_id)), trim(sku), NULLIF(trim(mapped_sku), ''), wrong_sku, status
FROM public.listing_data
WHERE market_place = 'UK' AND which_channel = ANY(%s) AND upper(trim(ref_id)) = ANY(%s)"""
SUFFIX_VARIANT = re.compile(r"_(AMN1?|AMD|AML|T|A)$")  # reported only, never stripped

MASTER_SQL = """
SELECT p.sku, i.sku, c.pack_count
FROM public.inv_product_combo c
JOIN public.inv_products p ON p.id = c.product
JOIN public.inv_products i ON i.id = c.inventory"""


def month_add(ym, n):
    y, m = map(int, ym.split("-"))
    m += n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def month_label(ym):
    y, m = map(int, ym.split("-"))
    return f"{calendar.month_abbr[m]}-{y}"


def with_retry(fn, what, attempts=3):
    """Run fn(); retry twice on error or empty result (PDF Step 9: retry twice, then alert)."""
    last = None
    for i in range(attempts):
        try:
            res = fn()
            if res:
                return res, i + 1
            last = "zero rows"
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            last = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            time.sleep(2)
    print(f"  !! {what}: failed after {attempts} attempts ({last})")
    return [], attempts


# ---------------------------------------------------------------- extract

def load_or_pull_raw(conn, ym):
    """Raw_<marketplace>.csv per month; written once, read back on every later run."""
    d = RAW_DIR / ym
    d.mkdir(parents=True, exist_ok=True)
    start = f"{ym}-01"
    end = f"{month_add(ym, 1)}-01"
    out, info = {}, {}
    for src, label, fname in MARKETPLACES:
        f = d / fname
        if f.exists():
            with f.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))  # every field stays text
            info[label] = {"origin": "stored", "attempts": 0, "file": str(f.relative_to(BASE))}
        else:
            recs, attempts = with_retry(lambda: conn.execute(RAW_SQL, (src, start, end)).fetchall(), f"{label} {ym}")
            rows = [dict(zip(RAW_COLS, ["" if v is None else v for v in r])) for r in recs]
            if rows:  # never persist an empty/failed pull
                with f.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=RAW_COLS)
                    w.writeheader()
                    w.writerows(rows)
            info[label] = {"origin": "pulled", "attempts": attempts, "file": str(f.relative_to(BASE)) if rows else None}
        out[label] = rows
    return out, info


def load_master(conn):
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as lc:
        pk = {str(i): int(q) for i, c, q in lc.execute("SELECT id, pack_char, pack_qty FROM inventory.product_pk")}
        pk_char = {str(c).upper(): int(q) for i, c, q in lc.execute("SELECT id, pack_char, pack_qty FROM inventory.product_pk")}
    rows = conn.execute(MASTER_SQL).fetchall()
    comps, bad = defaultdict(list), defaultdict(list)
    for psku, isku, code in rows:
        if code in pk:
            comps[psku].append((isku, pk[code]))
        else:
            bad[psku].append(code)
    snap = MASTER_DIR / dt.date.today().isoformat()
    snap.mkdir(parents=True, exist_ok=True)
    if not (snap / "combo_components.csv").exists():
        with (snap / "combo_components.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["product_sku", "component_sku", "pack_count_code", "component_qty"])
            for psku, isku, code in rows:
                w.writerow([psku, isku, code, pk.get(code, "")])
        (snap / "product_pk.json").write_text(json.dumps(pk_char, indent=1), encoding="utf-8")
    return dict(comps), dict(bad), pk_char, len(rows)


def load_listing(conn, need):
    """listing_data rows (UK) for the (marketplace, ID) pairs whose sold SKU is not in the product master."""
    ch_mp = {v: k for k, v in LISTING_CHANNEL.items()}
    ids = sorted({rid.upper() for _, rid in need})
    rows = conn.execute(LISTING_SQL, (list(LISTING_CHANNEL.values()), ids)).fetchall() if ids else []
    valid, wrong = defaultdict(list), set()
    for ch, rid, sku, mapped, w, st in rows:
        key = (ch_mp[ch], rid)
        if key not in need:
            continue
        if w == 0:
            valid[key].append((sku, mapped, st))
        else:
            wrong.add(key)
    snap = MASTER_DIR / dt.date.today().isoformat() / "listing_lookup.csv"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        with snap.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["which_channel", "ref_id", "sku", "mapped_sku", "wrong_sku", "status"])
            w.writerows(rows)
    return dict(valid), wrong, len(rows)


# PH (Portfolio Holder) allocation: approved source = ledsone staff master (= sheet "Master Amazon UK 3rd Cycle").
# The current allocation is applied to every reported month (approved business assumption). 4th Cycle tabs are not used.
PH_SQL = """
SELECT upper(trim(p.ref_id)), u.username, c.category_name, p.assign_date
FROM staff.ph_category_products p
JOIN staff.ph_categories c ON c.id = p.ph_category_id
JOIN staff.users u ON u.id = c.user_id
WHERE p.source_id = 1"""
NO_PH = "(No PH)"


def load_ph():
    """ASIN -> PH from the ledsone staff master; ASINs held by more than one PH are returned separately, never guessed."""
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"], autocommit=True) as lc:
        lc.execute("SET default_transaction_read_only = on")
        rows = lc.execute(PH_SQL).fetchall()
    owners = defaultdict(set)
    for asin, ph, cat, ad in rows:
        owners[asin].add(ph)
    snap = MASTER_DIR / dt.date.today().isoformat() / "ph_allocation.csv"
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        with snap.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["asin", "ph", "category", "assign_date"])
            w.writerows(rows)
    ph_map = {a: next(iter(p)) for a, p in owners.items() if len(p) == 1}
    multi = {a: sorted(p) for a, p in owners.items() if len(p) > 1}
    return ph_map, multi, rows


def validate_ph_asins(conn, res, owners):
    """Status of every allocated ASIN against UK Amazon listing_data + the product master (same validity rule as the resolver)."""
    lst = defaultdict(list)
    for ref, sku, mapped, w in conn.execute("""SELECT upper(trim(ref_id)), trim(sku), NULLIF(trim(mapped_sku), ''), wrong_sku
            FROM public.listing_data WHERE which_channel = 1 AND market_place = 'UK'"""):
        lst[ref].append((sku, mapped, w))
    status, valid = {}, {}
    for a in owners:
        if not lst.get(a):
            status[a] = ("PH ASIN not found in database", "No UK Amazon listing_data row for this ASIN")
            continue
        rows = [r for r in lst[a] if r[2] == 0]
        if not rows:
            status[a] = ("PH ASIN missing valid SKU", "Only wrong_sku=1 listing rows")
            continue
        targets = {(m or s) for s, m, _ in rows}
        ok = sorted(t for t in targets if res.resolve(t)["reason"] is None)
        valid[a] = set(ok)
        if len(ok) > 1:
            status[a] = ("PH ASIN maps to multiple SKUs", "Valid SKUs: " + ", ".join(ok[:6]))
        elif not ok:
            status[a] = ("PH ASIN missing valid SKU", "Listing target(s) not in product master: " + ", ".join(sorted(targets)[:6]))
        else:
            status[a] = ("OK", ok[0]) if len(targets) == 1 else ("OK (+ unmapped listing variant)", ok[0])
    return status, valid


# Product Category for Report 4 single SKUs (presentation only). No category exists on the product master
# (inv_products.sub_category is an unlabelled id); the only named product category is order_transaction.category_name,
# taken from the single SKU's OWN order lines. Conflicting or missing categories are shown as such, never guessed.
CAT_CONFLICT, CAT_NONE = "Category Conflict (review)", "Category Not Available"


def load_categories(skus):
    """1) the single SKU's own order lines; 2) if none carry a category, the order lines of the packs/combos that contain
    it (inv_product_combo). 'Special-*' values are PH segment labels, not product categories, and are ignored."""
    cat_sql = """SELECT sku, trim(category_name), count(*), max(order_date)::date FROM public.order_transaction
        WHERE sku = ANY(%s) AND trim(coalesce(category_name, '')) <> '' AND category_name NOT ILIKE 'Special-%%' GROUP BY 1, 2"""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("SET default_transaction_read_only = on")
        own = defaultdict(list)
        for sku, cat, n, last in c.execute(cat_sql, (sorted(skus),)):
            own[sku].append((cat, n, str(last)))
        rest = sorted(s for s in skus if s not in own)
        parents = defaultdict(set)
        for comp, parent in c.execute("""SELECT i.sku, p.sku FROM public.inv_product_combo x JOIN public.inv_products p ON p.id = x.product
                JOIN public.inv_products i ON i.id = x.inventory WHERE i.sku = ANY(%s) AND p.sku <> i.sku""", (rest,)):
            parents[comp].add(parent)
        pcat = defaultdict(list)
        for sku, cat, n, last in c.execute(cat_sql, (sorted({p for v in parents.values() for p in v}),)):
            pcat[sku].append((cat, n, str(last)))
    out = {}
    for sku in skus:
        if sku in own:
            cats, via = own[sku], "own order lines"
        else:
            agg = defaultdict(lambda: [0, ""])
            for p in parents.get(sku, ()):
                for cat, n, last in pcat.get(p, ()):
                    agg[cat][0] += n; agg[cat][1] = max(agg[cat][1], last)
            cats, via = [(k, v[0], v[1]) for k, v in agg.items()], f"pack/combo order lines ({len(parents.get(sku, ()))} parent SKUs)"
        cats = sorted(cats, key=lambda x: (x[1], x[2]), reverse=True)  # most lines first; tie -> most recently used
        if not cats:
            out[sku] = [CAT_NONE, "No order line of this SKU or of any pack/combo containing it carries a product category"]
        elif len(cats) == 1:
            out[sku] = [cats[0][0], f"order_transaction.category_name via {via} ({cats[0][1]} lines)"]
        else:  # approved: show the category with the most order lines; full list kept for audit
            out[sku] = [cats[0][0], f"most lines via {via}: " + "; ".join(f"{c} ({n} lines, last {d})" for c, n, d in cats)]
    return out


def load_scope(conn, ym):
    start, end = f"{ym}-01", f"{month_add(ym, 1)}-01"
    return conn.execute(SCOPE_SQL, (start, end)).fetchall()


# ---------------------------------------------------------------- mapping

class Resolver:
    """SKU (as sold) -> SKU Type, Pack Size, component units, or an exception reason."""
    SUFFIX = re.compile(r"^(.+?)-?([1-9A-S])PK$", re.I)  # WL001-3PK and LDST185B2242PK styles

    def __init__(self, comps, bad, pk_char):
        self.comps, self.bad, self.pk_char, self.cache = comps, bad, pk_char, {}

    def _part(self, part):
        """Resolve one '+' part to components, or None."""
        if part in self.comps and part not in self.bad:
            return self.comps[part]
        m = self.SUFFIX.match(part)
        if m and m.group(1) in self.comps and m.group(1) not in self.bad:
            n = self.pk_char[m.group(2).upper()]
            return [(c, q * n) for c, q in self.comps[m.group(1)]]
        return None

    def resolve(self, sku):
        if sku in self.cache:
            return self.cache[sku]
        r = self._resolve(sku)
        self.cache[sku] = r
        return r

    def attach_listing(self, valid, wrong):
        self.listing, self.listing_wrong, self.line_cache = valid, wrong, {}

    def resolve_line(self, mp, rid, sku):
        """Sold SKU through the product master first (unchanged); if it does not resolve,
        Marketplace + ID (+ sold SKU) through listing_data, then the mapped SKU through the product master."""
        m = self.resolve(sku)
        if m["r13"]:
            return m
        if mp not in LISTING_CHANNEL:
            if m["reason"] != "ID not in master":
                return m
            return dict(m, reason="SKU not in master (ID lookup not possible)",
                        detail=f"{mp} order lines carry no marketplace ID for listing_data; sold SKU not in product master")
        key = (mp, rid, sku)
        if key not in self.line_cache:
            self.line_cache[key] = self._resolve_listing(mp, rid, sku)
        return self.line_cache[key]

    def _resolve_listing(self, mp, rid, sku):
        def fail(reason, detail):
            return dict(type="", pack=None, comps=None, source="listing_data", reason=reason, detail=detail,
                        r13=False, r4=False)
        rows = self.listing.get((mp, rid), [])
        if not rows:
            extra = " (only wrong_sku=1 rows exist)" if (mp, rid) in self.listing_wrong else ""
            return fail("ID not in master", f"{mp} ID absent from listing_data UK wrong_sku=0{extra}; "
                                            "sold SKU not in product master")
        exact = [r for r in rows if r[0] == sku]
        basis, how = (exact, "ID + sold SKU") if exact else (rows, "ID only - sold SKU not listed")
        targets = {}
        for lsku, mapped, _ in basis:
            targets.setdefault(mapped or lsku, set()).add(lsku)
        ok = {t: self.resolve(t) for t in targets}
        ok = {t: v for t, v in ok.items() if v["reason"] is None}  # fully resolved only (not combo-without-components)
        bad = sorted(t for t in targets if t not in ok)
        listing = "; ".join(sorted(f"{l}→{m or '(mapped_sku blank)'}" for l, m, _ in basis))
        variants = [t for t in bad if SUFFIX_VARIANT.search(t)]
        confirm = f" · suffix variant(s) {', '.join(variants)}: business confirmation required" if variants else ""
        if len(ok) > 1:
            return fail("Conflicting mapping", f"{how}: ID maps to {len(ok)} valid SKUs [{listing}]{confirm}")
        if len(ok) == 1 and not bad:
            t, v = next(iter(ok.items()))
            return dict(v, mapped=t, source=f"listing_data ({how})")
        return fail("ID found, mapping incomplete",
                    f"{how}: target(s) not in product master: {', '.join(bad)}"
                    + (f"; valid target {', '.join(ok)} not applied (not deterministic)" if ok else "")
                    + f" [{listing}]{confirm}")

    def _resolve(self, sku):
        # returns dict(type, pack, comps, source, reason, r13, r4)
        if sku in self.bad:
            return dict(type="", pack=None, comps=None, source="master", reason="Unknown pack suffix",
                        detail=f"pack_count code(s) {self.bad[sku]} not in product_pk", r13=False, r4=False)
        if sku in self.comps:
            c = self.comps[sku]
            if "+" in sku or len(c) > 1:
                return dict(type="Combo", pack=1, comps=c, source="master", reason=None, r13=True, r4=True)
            (csku, q), = c
            if q > 1:
                return dict(type="Pack", pack=q, comps=c, source="master", reason=None, r13=True, r4=True)
            return dict(type="Single", pack=1, comps=c, source="master", reason=None, r13=True, r4=True)
        if "+" in sku:
            parts = [p.strip() for p in sku.split("+")]
            merged, missing = [], []
            for p in parts:
                pc = self._part(p) if p else None
                if pc is None:
                    missing.append(p or "(blank)")
                else:
                    merged.extend(pc)
            if missing:
                return dict(type="Combo", pack=1, comps=None, source="+ split", reason="Combo without components",
                            detail="Unresolvable component(s): " + ", ".join(missing), r13=True, r4=False)
            return dict(type="Combo", pack=1, comps=merged, source="+ split", reason=None, r13=True, r4=True)
        m = self.SUFFIX.match(sku)
        if m and m.group(1) in self.comps:
            n = self.pk_char[m.group(2).upper()]
            return dict(type="Pack", pack=n, comps=[(c, q * n) for c, q in self.comps[m.group(1)]],
                        source="suffix", reason=None, r13=True, r4=True)
        if re.search(r"PK$", sku, re.I):
            return dict(type="", pack=None, comps=None, source=None, reason="Unknown pack suffix",
                        detail="SKU ends in PK but pack size/base SKU cannot be read", r13=False, r4=False)
        return dict(type="", pack=None, comps=None, source=None, reason="ID not in master",
                    detail="SKU not in product master; no SKU Type / Pack Size", r13=False, r4=False)


def line_id(label, r):
    if label == "Amazon":
        return (r["asin"] or "").strip().upper()
    if label == "eBay":
        return (r["item_id"] or "").strip()
    return (r["sku"] or "").strip()  # B&Q: no product ID/barcode in order_transaction


# ---------------------------------------------------------------- build

def pct(cur, prev):
    if prev is None or prev == 0:
        return None
    return float(((cur - prev) / prev * 100).quantize(Decimal("0.1")))


def build(month):
    months = [month_add(month, -2), month_add(month, -1), month]
    pull = [month_add(month, -3)] + months
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    conn.execute("SET default_transaction_read_only = on")

    comps, bad, pk_char, master_rows = load_master(conn)
    res = Resolver(comps, bad, pk_char)
    print(f"master: {master_rows} component rows, {len(comps)} products, {len(bad)} with unknown pack code")

    raw, raw_info, scope = {}, {}, []
    for ym in pull:
        raw[ym], raw_info[ym] = load_or_pull_raw(conn, ym)
        for src, uk, st, n, q, amt in load_scope(conn, ym):
            scope.append(dict(month=ym, marketplace={"AMAZON": "Amazon", "EBAY": "eBay", "B&Q": "B&Q"}[src],
                              region="UK" if uk else "Non-UK", status=st, rows=n, qty=int(q), sales=float(amt)))
        print(f"raw {ym}: " + ", ".join(f"{k}={len(v)}" for k, v in raw[ym].items()))
    need = {(mp, line_id(mp, r)) for ym in pull for mp, rows in raw[ym].items() if mp in LISTING_CHANNEL
            for r in rows if r["sku"].strip() and not res.resolve(r["sku"].strip())["r13"]}
    listing, listing_wrong, listing_rows = load_listing(conn, need)
    res.attach_listing(listing, listing_wrong)
    print(f"listing_data: {len(need)} IDs needed a lookup, {listing_rows} rows returned, {len(listing)} IDs with wrong_sku=0 rows")
    ph_map, ph_multi, ph_rows = load_ph()
    ph_owners = {a: [p] for a, p in ph_map.items()} | ph_multi
    ph_status, ph_valid_skus = validate_ph_asins(conn, res, ph_owners)
    ph_names = sorted({r[1] for r in ph_rows})
    print(f"PH master: {len(ph_rows)} rows, {len(ph_owners)} ASINs, {len(ph_names)} PHs, {len(ph_multi)} ASINs with >1 PH")
    conn.close()
    ph_r1 = defaultdict(lambda: [Decimal(0), Decimal(0)])     # (ym, ph, sku) -> [qty, sales]
    ph_asin = defaultdict(lambda: [Decimal(0), Decimal(0), set()])  # (ym, ph, asin, sku) -> [qty, sales, mapped SKUs]
    ph_r4 = defaultdict(Decimal)                              # (ym, ph, single) -> Amazon units

    exc = []                    # exception rows
    agg = {}                    # (ym, mp, sku) -> dict
    r4 = defaultdict(lambda: defaultdict(Decimal))       # (ym, single) -> {mp: units}
    combo_trail = defaultdict(lambda: [Decimal(0), Decimal(0)])  # (ym, mp, combo, comp) -> [combo qty, units]
    pack_trail = defaultdict(lambda: [Decimal(0), Decimal(0)])   # (ym, mp, pack, single) -> [pack qty, units]
    recon13, recon4 = [], []
    id_skus = defaultdict(set)

    for ym in pull:
        for _, mp, _ in MARKETPLACES:
            rows = raw[ym][mp]
            raw_q = sum(Decimal(r["quantity"]) for r in rows)
            raw_s = sum(Decimal(r["order_total"]) for r in rows)
            ex_q = ex_s = Decimal(0)
            seen, grouped_exc = set(), defaultdict(lambda: [Decimal(0), Decimal(0), 0, ""])
            cat = defaultdict(Decimal)   # Report-4 expected units by SKU type
            r4_excl_q = Decimal(0)
            if not rows:
                exc.append(dict(type="Missing/zero marketplace data", mp=mp, month=ym, id="", sku="", qty=0, sales=0,
                                details="Marketplace extract returned zero rows after 3 attempts",
                                impact="Report empty - REVIEW; email alert not configured", status="REVIEW"))
            for r in rows:
                q, s = Decimal(r["quantity"]), Decimal(r["order_total"])
                sku = (r["sku"] or "").strip()
                rid = line_id(mp, r)
                key = tuple((r[k] or "").strip() for k in DUP_KEY)
                if key in seen:
                    g = grouped_exc[("Exact duplicate removed", rid, sku, "Duplicate of an identical order line")]
                    g[0] += q; g[1] += s; g[2] += 1
                    ex_q += q; ex_s += s
                    continue
                seen.add(key)
                if not sku:
                    g = grouped_exc[("Missing SKU", rid, "", "ID has no SKU")]
                    g[0] += q; g[1] += s; g[2] += 1
                    ex_q += q; ex_s += s
                    continue
                m = res.resolve_line(mp, rid, sku)
                if not m["r13"]:
                    g = grouped_exc[(m["reason"], rid, sku, m["detail"])]
                    g[0] += q; g[1] += s; g[2] += 1
                    ex_q += q; ex_s += s
                    continue
                if mp == "Amazon":
                    id_skus[(ym, rid)].add(sku)
                a = agg.setdefault((ym, mp, sku), dict(qty=Decimal(0), sales=Decimal(0), type=m["type"],
                                                       pack=m["pack"], src=m["source"], ids=set(), lines=0,
                                                       mapped=set()))
                a["qty"] += q; a["sales"] += s; a["ids"].add(rid); a["lines"] += 1
                a["mapped"].add(m.get("mapped", sku))  # internal mapping only; the row stays keyed by the sold SKU
                ph = None
                if mp == "Amazon":  # PH is an extra reporting dimension on lines already in Report 1; nothing else changes
                    ph = ph_map.get(rid, NO_PH)
                    t = ph_r1[(ym, ph, sku)]; t[0] += q; t[1] += s
                    t = ph_asin[(ym, ph, rid, sku)]; t[0] += q; t[1] += s; t[2].add(m.get("mapped", sku))
                if not m["r4"]:
                    g = grouped_exc[(m["reason"], rid, sku, m["detail"])]
                    g[0] += q; g[1] += s; g[2] += 1
                    r4_excl_q += q
                    continue
                factor = sum(cq for _, cq in m["comps"])
                cat[m["type"]] += q * factor
                for csku, cq in m["comps"]:
                    r4[(ym, csku)][mp] += q * cq
                    if ph is not None:
                        ph_r4[(ym, ph, csku)] += q * cq
                    if m["type"] == "Combo":
                        t = combo_trail[(ym, mp, sku, csku)]; t[0] += q; t[1] += q * cq
                    elif m["type"] == "Pack":
                        t = pack_trail[(ym, mp, sku, csku)]; t[0] += q; t[1] += q * cq

            for (reason, rid, sku, detail), (q, s, n, _) in grouped_exc.items():
                in13 = reason == "Combo without components"
                exc.append(dict(type=reason, mp=mp, month=ym, id=rid, sku=sku, qty=int(q), sales=float(s),
                                details=f"{detail} ({n} line{'s' if n != 1 else ''})",
                                impact="Kept in Reports 1-3; left out of Report 4" if in13 else
                                "Excluded; flagged for master mapping fix" if reason == "Conflicting mapping" else
                                "Excluded from all reports",
                                status="Flagged" if in13 else "Excluded"))
            rep_q = sum(v["qty"] for k, v in agg.items() if k[0] == ym and k[1] == mp)
            rep_s = sum(v["sales"] for k, v in agg.items() if k[0] == ym and k[1] == mp)
            dq, ds = raw_q - ex_q - rep_q, raw_s - ex_s - rep_s
            recon13.append(dict(month=ym, mp=mp, raw_rows=len(rows), raw_qty=int(raw_q), raw_sales=float(raw_s),
                                exc_qty=int(ex_q), exc_sales=float(ex_s), rep_qty=int(rep_q), rep_sales=float(rep_s),
                                diff_qty=int(dq), diff_sales=float(ds),
                                status="PASS" if dq == 0 and abs(ds) < Q2 and rows else "REVIEW"))
            # Report 4 check: expected from per-SKU factors vs units actually posted to single SKUs
            actual = sum(v[mp] for k, v in r4.items() if k[0] == ym)
            expected = cat["Single"] + cat["Pack"] + cat["Combo"]
            recon4.append(dict(month=ym, mp=mp, singles=int(cat["Single"]), pack_units=int(cat["Pack"]),
                               combo_units=int(cat["Combo"]), expected=int(expected), report4=int(actual),
                               combo_excluded_qty=int(r4_excl_q), diff=int(actual - expected),
                               status="PASS" if actual == expected and rows else "REVIEW"))

    # Amazon ASINs carrying more than one seller SKU (informational - see assumptions)
    for (ym, rid), skus in id_skus.items():
        if len(skus) > 1 and ym in months:
            q = sum(agg[(ym, "Amazon", s)]["qty"] for s in skus)
            exc.append(dict(type="ID shared by several SKUs (review)", mp="Amazon", month=ym, id=rid,
                            sku=" | ".join(sorted(skus)), qty=0, sales=0,
                            details=f"ASIN sold under {len(skus)} seller SKUs; each line kept on its own SKU",
                            impact="Included - listing identity is ASIN + seller SKU", status="Review"))

    # ---- Reports 1-3 rows with Avg Price + MoM + movement flag
    r13_rows = []
    for (ym, mp, sku), a in agg.items():
        if ym not in months:
            continue
        p = agg.get((month_add(ym, -1), mp, sku))
        pq, ps = (p["qty"], p["sales"]) if p else (None, None)
        mq, ms = pct(a["qty"], pq), pct(a["sales"], ps)
        move = (mq is not None and abs(mq) > MOVE_PCT) or (ms is not None and abs(ms) > MOVE_PCT)
        r13_rows.append([ym, mp, sku, a["type"], a["pack"], int(a["qty"]), float(a["sales"]),
                         float((a["sales"] / a["qty"]).quantize(Q2)) if a["qty"] else None,
                         int(pq) if pq is not None else None, float(ps) if ps is not None else None, mq, ms,
                         1 if move else 0, len(a["ids"]), " | ".join(sorted(a["ids"])[:6]) + (" ..." if len(a["ids"]) > 6 else ""),
                         a["src"], " | ".join(sorted(a["mapped"])) if a["mapped"] != {sku} else ""])
        if move:
            exc.append(dict(type="Unusual movement (>±50% MoM)", mp=mp, month=ym, id="", sku=sku, qty=int(a["qty"]),
                            sales=float(a["sales"]),
                            details=f"Qty {int(pq)}→{int(a['qty'])} ({'n/a' if mq is None else f'{mq:+.1f}%'}) · "
                                    f"Sales £{ps:,.2f}→£{a['sales']:,.2f} ({'n/a' if ms is None else f'{ms:+.1f}%'})",
                            impact="Included - row highlighted for review", status="Highlight"))

    # ---- Report 4 rows
    r4_rows = []
    for (ym, single), u in r4.items():
        if ym not in months:
            continue
        tot = u["Amazon"] + u["eBay"] + u["B&Q"]
        days = calendar.monthrange(*map(int, ym.split("-")))[1]
        prev = r4.get((month_add(ym, -1), single))
        ptot = (prev["Amazon"] + prev["eBay"] + prev["B&Q"]) if prev else None
        r4_rows.append([ym, single, int(u["Amazon"]), int(u["eBay"]), int(u["B&Q"]), int(tot),
                        float((tot / days).quantize(Decimal("0.01"))), int(ptot) if ptot is not None else None,
                        pct(tot, ptot)])

    # ---- PH layer (Amazon only): datasets, PH exceptions, PH reconciliation. Built from lines already in Report 1.
    asin_idx = defaultdict(set)
    for (ym, ph, asin, sku) in ph_asin:
        asin_idx[(ym, ph, sku)].add(asin)
    ph_r1_rows = []
    for (ym, ph, sku), (q, s) in ph_r1.items():
        if ym not in months:
            continue
        a = agg[(ym, "Amazon", sku)]
        asins = sorted(asin_idx[(ym, ph, sku)])
        p = ph_r1.get((month_add(ym, -1), ph, sku))  # .get: never creates keys in the defaultdict
        pq, ps = (p[0], p[1]) if p else (None, None)
        mq, ms = pct(q, pq), pct(s, ps)
        move = (mq is not None and abs(mq) > MOVE_PCT) or (ms is not None and abs(ms) > MOVE_PCT)
        ph_r1_rows.append([ym, ph, sku, a["type"], a["pack"], int(q), float(s), len(asins), " | ".join(asins[:6]) + (" ..." if len(asins) > 6 else ""),
                           int(pq) if pq is not None else None, float(ps) if ps is not None else None, mq, ms, 1 if move else 0])
    ph_tot = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for (ym, ph, sku), (q, s) in ph_r1.items():
        t = ph_tot[(ym, ph)]; t[0] += q; t[1] += s
    ph_month = []
    for (ym, ph), (q, s) in ph_tot.items():
        if ym in months:
            p = ph_tot.get((month_add(ym, -1), ph))
            ph_month.append([ym, ph, int(q), float(s), int(p[0]) if p else None, float(p[1]) if p else None,
                             pct(q, p[0] if p else None), pct(s, p[1] if p else None)])
    ph_asin_rows = [[ym, ph, asin, sku, agg[(ym, "Amazon", sku)]["type"], int(q), float(s), " | ".join(sorted(mp_)),
                     ph_status.get(asin, ("Not allocated", ""))[0]]
                    for (ym, ph, asin, sku), (q, s, mp_) in ph_asin.items() if ym in months]
    r4_by = {(ym, single): u for (ym, single), u in r4.items()}
    ph_r4_rows = []
    for (ym, ph, single), units in ph_r4.items():
        if ym not in months:
            continue
        u = r4_by[(ym, single)]
        tot = u["Amazon"] + u["eBay"] + u["B&Q"]
        days = calendar.monthrange(*map(int, ym.split("-")))[1]
        ph_r4_rows.append([ym, ph, single, int(units), int(u["eBay"]), int(u["B&Q"]), int(tot),
                           float((tot / days).quantize(Decimal("0.01"))), int(u["Amazon"])])

    rm = months[-1]
    for asin, phs in ph_multi.items():
        exc.append(dict(type="ASIN allocated to multiple PHs", mp="Amazon", month=rm, id=asin, sku="", qty=0, sales=0,
                        details="PHs: " + ", ".join(phs), impact="Not attributed to any PH until resolved", status="Review"))
    sold_by_asin = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for (ym, ph, asin, sku), (q, s, _) in ph_asin.items():
        if ym == rm:
            t = sold_by_asin[asin]; t[0] += q; t[1] += s
    for asin, (st, det) in sorted(ph_status.items()):
        if st.startswith("OK"):
            continue
        q, s = sold_by_asin.get(asin, (0, 0))
        exc.append(dict(type=st, mp="Amazon", month=rm, id=asin, sku="", qty=int(q), sales=float(s),
                        details=f"PH {', '.join(ph_owners[asin])} · {det}",
                        impact="Allocation check only - sales and units are not changed", status="Review"))
    for ym in months:
        unal = defaultdict(lambda: [Decimal(0), Decimal(0), set()])
        for (y, ph, asin, sku), (q, s, _) in ph_asin.items():
            if y != ym:
                continue
            if ph == NO_PH:
                t = unal[asin]; t[0] += q; t[1] += s; t[2].add(sku)
            else:
                listing_skus = ph_valid_skus.get(asin, set())
                mapped = ph_asin[(y, ph, asin, sku)][2]
                if listing_skus and not (listing_skus & mapped):  # sold SKU is none of the ASIN's valid listing SKUs
                    exc.append(dict(type="PH/SKU mapping conflict", mp="Amazon", month=ym, id=asin, sku=sku, qty=int(q), sales=float(s),
                                    details=f"PH {ph}: sold/mapped SKU {', '.join(sorted(mapped))} vs allocation listing SKU(s) {', '.join(sorted(listing_skus))}",
                                    impact="Included under this PH - review mapping", status="Review"))
        for asin, (q, s, skus) in unal.items():
            why = " (ASIN held by several PHs)" if asin in ph_multi else ""
            exc.append(dict(type="No PH allocation", mp="Amazon", month=ym, id=asin, sku=" | ".join(sorted(skus)), qty=int(q), sales=float(s),
                            details=f"ASIN not in the PH master{why}", impact="Included in Amazon totals; not attributed to any PH",
                            status="Flagged"))

    for e in exc:  # PH tag for display filtering only (Amazon ASIN-level rows); SKU-level rows (id "") stay untagged
        if e["mp"] == "Amazon" and e["id"]:
            e["ph"] = NO_PH if e["type"] == "No PH allocation" else ", ".join(ph_owners.get(e["id"], [NO_PH]))

    recon_ph = []
    for ym in months:
        r1q = sum(v["qty"] for k, v in agg.items() if k[0] == ym and k[1] == "Amazon")
        r1s = sum(v["sales"] for k, v in agg.items() if k[0] == ym and k[1] == "Amazon")
        pq = sum(v[0] for k, v in ph_r1.items() if k[0] == ym and k[1] != NO_PH)
        ps = sum(v[1] for k, v in ph_r1.items() if k[0] == ym and k[1] != NO_PH)
        uq = sum(v[0] for k, v in ph_r1.items() if k[0] == ym and k[1] == NO_PH)
        us = sum(v[1] for k, v in ph_r1.items() if k[0] == ym and k[1] == NO_PH)
        r4a = sum(u["Amazon"] for (y, _), u in r4.items() if y == ym)
        p4 = sum(v for k, v in ph_r4.items() if k[0] == ym and k[1] != NO_PH)
        u4 = sum(v for k, v in ph_r4.items() if k[0] == ym and k[1] == NO_PH)
        ok = pq + uq == r1q and abs(ps + us - r1s) < Q2 and p4 + u4 == r4a
        recon_ph.append(dict(month=ym, r1_qty=int(r1q), r1_sales=float(r1s), ph_qty=int(pq), ph_sales=float(ps),
                             noph_qty=int(uq), noph_sales=float(us), diff_qty=int(r1q - pq - uq), diff_sales=float(r1s - ps - us),
                             r4_amazon=int(r4a), ph_units=int(p4), noph_units=int(u4), diff_units=int(r4a - p4 - u4),
                             phs_with_sales=len({k[1] for k in ph_r1 if k[0] == ym and k[1] != NO_PH}),
                             status="PASS" if ok else "REVIEW"))

    ph_valid = []
    for ph in ph_names + [NO_PH]:
        mine = [a for a, o in ph_owners.items() if ph in o]
        c = Counter(ph_status[a][0] for a in mine)
        row = dict(ph=ph, allocated=len(mine), ok=c["OK"], ok_variant=c["OK (+ unmapped listing variant)"],
                   missing_sku=c["PH ASIN missing valid SKU"], multi_sku=c["PH ASIN maps to multiple SKUs"],
                   not_found=c["PH ASIN not found in database"], multi_ph=sum(1 for a in mine if a in ph_multi))
        for ym in months:
            row[ym] = dict(asins=len({k[2] for k in ph_asin if k[0] == ym and k[1] == ph}),
                           qty=int(sum(v[0] for k, v in ph_r1.items() if k[0] == ym and k[1] == ph)),
                           sales=float(sum(v[1] for k, v in ph_r1.items() if k[0] == ym and k[1] == ph)),
                           units=int(sum(v for k, v in ph_r4.items() if k[0] == ym and k[1] == ph)))
        ph_valid.append(row)

    # ---- Product Category (display grouping of Report 4 only; units are read from the finished r4 / ph_r4 rows)
    sku_cat = load_categories({r[1] for r in r4_rows})
    recon_cat = []
    for ym in months:
        rows = [r for r in r4_rows if r[0] == ym]
        cat_tot = defaultdict(lambda: [0, 0, 0, 0, 0])
        for r in rows:
            t = cat_tot[sku_cat[r[1]][0]]; t[0] += 1; t[1] += r[2]; t[2] += r[3]; t[3] += r[4]; t[4] += r[5]
        sums = [sum(v[i] for v in cat_tot.values()) for i in range(5)]
        base = [len(rows), sum(r[2] for r in rows), sum(r[3] for r in rows), sum(r[4] for r in rows), sum(r[5] for r in rows)]
        recon_cat.append(dict(month=ym, categories=len(cat_tot), skus=base[0], amazon=base[1], ebay=base[2], bq=base[3], total=base[4],
                              cat_skus=sums[0], cat_amazon=sums[1], cat_ebay=sums[2], cat_bq=sums[3], cat_total=sums[4],
                              conflict_skus=cat_tot[CAT_CONFLICT][0] if CAT_CONFLICT in cat_tot else 0,
                              none_skus=cat_tot[CAT_NONE][0] if CAT_NONE in cat_tot else 0,
                              status="PASS" if sums == base else "REVIEW"))

    combo_rows = [[ym, mp, c, comp, int(v[0]), int(v[1])] for (ym, mp, c, comp), v in combo_trail.items() if ym in months]
    pack_rows = [[ym, mp, p, s, int(v[0]), int(v[1])] for (ym, mp, p, s), v in pack_trail.items() if ym in months]
    exc = [e for e in exc if e["month"] in months]
    recon13 = [r for r in recon13 if r["month"] in months]
    recon4 = [r for r in recon4 if r["month"] in months]
    scope = [s for s in scope if s["month"] in months]

    overall = "PASS" if all(r["status"] == "PASS" for r in recon13 + recon4 + recon_ph + recon_cat) else "REVIEW"
    meta = dict(report_month=month, report_label=month_label(month), months=months,
                month_labels={m: month_label(m) for m in months},
                days={m: calendar.monthrange(*map(int, m.split("-")))[1] for m in months},
                generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), status=overall,
                raw_info={m: raw_info[m] for m in months}, master_rows=master_rows,
                master_products=len(comps), pack_codes=pk_char,
                ph=dict(source="ledsone staff.ph_category_products → ph_categories → users (source_id 1 = Amazon); "
                               "identical to sheet tab 'Master Amazon UK 3rd Cycle'",
                        phs=ph_names, asins=len(ph_owners), multi_ph=len(ph_multi),
                        assign_dates=sorted({str(r[3]) for r in ph_rows}),
                        status_counts=dict(Counter(v[0] for v in ph_status.values()))))
    data = dict(meta=meta, r13=r13_rows, r4=r4_rows, combo=combo_rows, pack=pack_rows, exc=exc,
                recon13=recon13, recon4=recon4, scope=scope,
                sku_cat=sku_cat, recon_cat=recon_cat,
                ph_r1=ph_r1_rows, ph_month=ph_month, ph_asin=ph_asin_rows, ph_r4=ph_r4_rows, ph_valid=ph_valid, recon_ph=recon_ph)
    return data


def main():
    ap = argparse.ArgumentParser()
    today = dt.date.today()
    default = month_add(f"{today.year:04d}-{today.month:02d}", -1)
    ap.add_argument("--month", default=default)
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", args.month) or args.month >= f"{today.year:04d}-{today.month:02d}":
        sys.exit(f"--month must be a completed month before {today:%Y-%m}")
    data = build(args.month)
    OUT_DIR.mkdir(exist_ok=True)
    prev = [OUT_DIR / f"data_{args.month}.json", OUT_DIR / f"Product_Performance_{args.month}.html"]
    if any(p.exists() for p in prev):  # append-only history: keep every previous result before it is replaced
        hist = OUT_DIR / "history" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        hist.mkdir(parents=True, exist_ok=False)
        for p in prev:
            if p.exists():
                shutil.copy2(p, hist / p.name)
    (OUT_DIR / f"data_{args.month}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    out = OUT_DIR / f"Product_Performance_{args.month}.html"
    out.write_text(html, encoding="utf-8")
    print(f"status={data['meta']['status']}  r13={len(data['r13'])} r4={len(data['r4'])} exc={len(data['exc'])}")
    for r in data["recon13"]:
        print("R1-3", r["month"], r["mp"], r["status"], "raw", r["raw_qty"], r["raw_sales"], "exc", r["exc_qty"],
              r["exc_sales"], "rep", r["rep_qty"], r["rep_sales"])
    for r in data["recon4"]:
        print("R4  ", r["month"], r["mp"], r["status"], r["singles"], r["pack_units"], r["combo_units"],
              r["expected"], r["report4"], "combo_excl_qty", r["combo_excluded_qty"])
    for r in data["recon_ph"]:
        print("PH  ", r["month"], r["status"], "R1", r["r1_qty"], r["r1_sales"], "= PH", r["ph_qty"], r["ph_sales"],
              "+ NoPH", r["noph_qty"], r["noph_sales"], "| R4 Amazon", r["r4_amazon"], "= PH", r["ph_units"], "+ NoPH", r["noph_units"],
              "| PHs with sales", r["phs_with_sales"])
    print("wrote", out, f"{out.stat().st_size/1e6:.1f} MB")


if __name__ == "__main__":
    main()
