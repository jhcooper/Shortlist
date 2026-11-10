"""Shared helpers for the job-search run. Standard library only."""
import json
import os
import re
import datetime as dt
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")          # per-run scratch (gitignored)
ET = ZoneInfo("America/New_York")
# Personal settings and run state live outside the engine (shortlist/config.py: CONFIG_DIR, STATE_DIR).


def dp(*parts):
    """Path inside the run-state folder (CONFIG_DIR/state by default): tracker mirror, seen postings, logs."""
    from shortlist.config import spath
    return spath(*parts)


def tracker_url():
    from shortlist.config import get
    return get("preferences.integrations.tracker_url", "") or ""


def recipient():
    from shortlist.config import get
    return get("preferences.digest.recipient")


def p(*parts):
    return os.path.join(ROOT, *parts)


def outp(*parts):
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, *parts)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(v, list):
                rows.extend(x for x in v if isinstance(x, dict))
            elif isinstance(v, dict):
                rows.append(v)
    return rows


def now_et():
    return dt.datetime.now(ET)


def parse_ts(v):
    """ISO timestamp (Z, offset, or naive = Eastern) or date -> aware datetime in Eastern; None if unparseable."""
    if isinstance(v, dt.datetime):
        d = v
    else:
        s = str(v or "").strip()
        if not s:
            return None
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=ET)
    return d.astimezone(ET)


def local_date(ts=None):
    """YYYY-MM-DD in America/New_York. ts: None (now), a datetime, or an ISO string; a bare YYYY-MM-DD is kept as is.
    Every date the run stores goes through this, so 9 pm Eastern (already tomorrow in UTC) still gets today's date."""
    if ts is None:
        return now_et().date().isoformat()
    if isinstance(ts, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts.strip()):
        return ts.strip()
    d = parse_ts(ts)
    return d.date().isoformat() if d else ""


def ts_after(a, b):
    """True when timestamp a is later than b. Compares real instants, so "...Z" (the page) and "...-05:00" (the run) mix safely."""
    da, db = parse_ts(a), parse_ts(b)
    return bool(da and db and da > db)


def today():
    return local_date()


def iso_now():
    return now_et().isoformat(timespec="seconds")


# Normalizers moved to shortlist/core/normalize.py; re-exported here so existing imports keep working.
from shortlist.core.normalize import (  # noqa: E402,F401
    _NOISE, _STATES, _STOP, TrackedIndex, dedupe_key, norm_company, norm_location, norm_title, norm_url, row_key, slug,
)


def row_id(company, role, date=None):
    return slug(company, role) + "-" + (date or today()).replace("-", "")
