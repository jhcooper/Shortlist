"""Labeled prefilter cases. Run: python3 tests/test_prefilter.py  (exit 1 on any failure)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("CONFIG_DIR", os.path.join(HERE, "..", "config.example"))  # fictional person; set CONFIG_DIR to test yours
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import prefilter as P
import datetime as dt
from common import ET
# Pin the clock: start dates in a year before the current one are ignored, so cases like "Start date: June 2026"
# need a fixed "today" to keep testing the cohort rule after 2026 ends.
P.now_et = lambda: dt.datetime(2026, 11, 6, 9, 0, tzinfo=ET)

fails = 0
# PREFILTER_CASES: a config repo can keep its own labeled cases (cases depend on whose preferences they test)
cases = json.load(open(os.environ.get("PREFILTER_CASES") or os.path.join(HERE, "prefilter_cases.json")))
for case in cases:
    c = P.normalize(case["in"]); reason, flags = P.rules(c); e = case["expect"]; bad = []
    if e["keep"] != (reason is None): bad.append(f"keep={reason is None} ({reason})")
    if "goals" in e and c.get("goals") != e["goals"]: bad.append(f"goals={c.get('goals')}")
    for k in ("goals_has", "goals_has2"):
        if k in e and e[k] not in (c.get("goals") or []): bad.append(f"goals={c.get('goals')} missing {e[k]}")
    for f in e.get("flags_has", []):
        if f not in flags: bad.append(f"flags={flags} missing {f}")
    if "work_mode" in e and c["work_mode"] != e["work_mode"]: bad.append(f"work_mode={c['work_mode']!r}")
    if "reason_has" in e and (not reason or e["reason_has"] not in reason): bad.append(f"reason={reason!r}")
    if bad:
        fails += 1; print("FAIL", case["name"], "|", "; ".join(bad))
# The past-year backstop follows the clock: in 2027 a June 2026 start date no longer counts.
n_extra = 0
for today, text, want in [((2026, 11, 6), "Start date: June 2026", [("June", "2026")]),
                          ((2027, 2, 1), "Start date: June 2026", []),
                          ((2027, 2, 1), "Start date: June 2027", [("June", "2027")])]:
    n_extra += 1
    P.now_et = lambda d=today: dt.datetime(*d, 9, 0, tzinfo=ET)
    got = P.start_dates(text)
    if got != want:
        fails += 1; print("FAIL start_dates", today, repr(text), "->", got, "expected", want)
print(f"{len(cases) + n_extra - fails}/{len(cases) + n_extra} passed")
sys.exit(1 if fails else 0)
