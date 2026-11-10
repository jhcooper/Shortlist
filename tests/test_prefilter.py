"""Labeled prefilter cases. PREFILTER_CASES: a config repo can keep its own labeled cases (they depend on whose
preferences they test); set CONFIG_DIR to match."""
import datetime as dt
import json
import os

import pytest

from shortlist.common import ET
from shortlist.core import prefilter as P

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = json.load(open(os.environ.get("PREFILTER_CASES") or os.path.join(HERE, "prefilter_cases.json"), encoding="utf-8"))


def pin_clock(monkeypatch, y, m, d):
    # Start dates in a year before the current one are ignored, so cases like "Start date: June 2026" need a fixed
    # "today" to keep testing the cohort rule after 2026 ends.
    monkeypatch.setattr(P, "now_et", lambda: dt.datetime(y, m, d, 9, 0, tzinfo=ET))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_labeled_case(case, monkeypatch):
    pin_clock(monkeypatch, 2026, 11, 6)
    c = P.normalize(case["in"])
    reason, flags = P.rules(c)
    e = case["expect"]
    bad = []
    if e["keep"] != (reason is None):
        bad.append(f"keep={reason is None} ({reason})")
    if "goals" in e and c.get("goals") != e["goals"]:
        bad.append(f"goals={c.get('goals')}")
    for k in ("goals_has", "goals_has2"):
        if k in e and e[k] not in (c.get("goals") or []):
            bad.append(f"goals={c.get('goals')} missing {e[k]}")
    for f in e.get("flags_has", []):
        if f not in flags:
            bad.append(f"flags={flags} missing {f}")
    if "work_mode" in e and c["work_mode"] != e["work_mode"]:
        bad.append(f"work_mode={c['work_mode']!r}")
    if "reason_has" in e and (not reason or e["reason_has"] not in reason):
        bad.append(f"reason={reason!r}")
    assert not bad, "; ".join(bad)


# The past-year backstop follows the clock: in 2027 a June 2026 start date no longer counts.
@pytest.mark.parametrize(("today", "text", "want"), [
    ((2026, 11, 6), "Start date: June 2026", [("June", "2026")]),
    ((2027, 2, 1), "Start date: June 2026", []),
    ((2027, 2, 1), "Start date: June 2027", [("June", "2027")]),
])
def test_start_dates_follow_the_clock(today, text, want, monkeypatch):
    pin_clock(monkeypatch, *today)
    assert P.start_dates(text) == want
