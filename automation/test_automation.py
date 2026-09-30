"""Offline tests for the monthly automation (no database, no publishing, real history untouched).

  python test_automation.py
"""
import copy, datetime as dt, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config, outputs, run, validate_output as vo  # noqa: E401,E402

fails = 0


def check(name, cond):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name}")


# 1. reporting month from the run date (3rd of the month -> previous full month)
for d, m in (("2026-10-03", "2026-09"), ("2026-11-03", "2026-10"), ("2026-12-03", "2026-11"), ("2027-01-03", "2026-12")):
    check(f"run {d} -> {m}", run.previous_full_month(dt.date.fromisoformat(d)) == m)

month = "2026-08"
data = json.loads((config.OUTPUT_DIR / f"data_{month}.json").read_text(encoding="utf-8"))
html = (config.OUTPUT_DIR / f"Product_Performance_{month}.html").read_text(encoding="utf-8")
ok = lambda d: {n: o for n, _, o, _ in vo.validate(month, d, html)}
check("validated build: all output checks PASS", all(ok(data).values()))

# 2. a reconciliation failure is detected (report must not be distributed)
bad = copy.deepcopy(data); bad["recon13"][-1]["status"] = "REVIEW"; bad["recon13"][-1]["diff_qty"] = 5
r = ok(bad); check("reconciliation failure -> check 11 (and 3) FAIL", not r[11] and not r[3])
# 3. a converted / divided Sales Amount is detected
bad = copy.deepcopy(data)
row = next(x for x in bad["r13"] if x[0] == month and x[1] == "Amazon"); row[6] = round(row[6] / 2, 2)
check("divided Sales Amount -> check 5 FAIL", not ok(bad)[5])
# 4. a wrong MoM is detected
bad = copy.deepcopy(data)
row = next(x for x in bad["r13"] if x[0] == month and x[10] is not None); row[10] += 1
check("wrong MoM -> check 10 FAIL", not ok(bad)[10])
# 5. sales leaking into Report 4 is detected
bad = copy.deepcopy(data)
i = next(k for k, x in enumerate(bad["r4"]) if x[0] == month); bad["r4"][i] = bad["r4"][i] + [123.45]
check("sales field in Report 4 -> check 8 FAIL", not ok(bad)[8])
# 6. missing marketplace data is detected
bad = copy.deepcopy(data)
for x in bad["recon13"]:
    if x["month"] == month and x["mp"] == "B&Q":
        x["raw_rows"] = 0
check("zero B&Q rows -> check 1 FAIL", not ok(bad)[1])

# 7. history is append-only (temporary copy of the master)
real = config.HISTORY_MASTER
with tempfile.TemporaryDirectory() as t:
    config.HISTORY_MASTER = Path(t) / "History_master.csv"
    m1 = outputs.append_history(month, data, "t1")
    m2 = outputs.append_history(month, data, "t2")
    changed = copy.deepcopy(data); next(x for x in changed["r13"] if x[0] == month)[5] += 1
    m3 = outputs.append_history(month, changed, "t3")
    rows = vo.read_history()
    check("first run appends", m1[0].startswith("APPENDED"))
    check("identical re-run does not append", m2 == ("ALREADY PRESENT - identical", True) and len(rows) == int(m1[0].split()[1]))
    check("different re-run is flagged, not overwritten", m3 == ("ALREADY PRESENT - DIFFERENT (not overwritten)", False))
config.HISTORY_MASTER = real

print(f"\n{'ALL TESTS PASS' if not fails else f'{fails} TEST(S) FAILED'}")
sys.exit(1 if fails else 0)
