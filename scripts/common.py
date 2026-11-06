"""Shared helpers for the job-search run. Standard library only."""
import json, os, re, datetime as dt
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")          # per-run scratch (gitignored)
ET = ZoneInfo("America/New_York")
# Personal settings and run state live outside the engine (scripts/config.py: CONFIG_DIR, STATE_DIR).


def dp(*parts):
    """Path inside the run-state folder (CONFIG_DIR/state by default): tracker mirror, seen postings, logs."""
    from config import spath
    return spath(*parts)


def tracker_url():
    from config import get
    return get("preferences.integrations.tracker_url", "") or ""


def recipient():
    from config import get
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


_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "at", "i", "ii", "iii", "-", "–", "—"}
_NOISE = re.compile(r"\b(20\d\d|new grad(uate)?|early career|entry[- ]level|remote|hybrid|on[- ]site|full[- ]time|part[- ]time|start|program(me)?)\b")


def norm_company(s):
    s = (s or "").lower()
    s = re.sub(r"[,.]|\b(inc|llc|ltd|corp|corporation|co|company|the|group|holdings)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in _STOP]
    return " ".join(words)


def dedupe_key(company, title):
    return f"{norm_company(company)}|{norm_title(title)}"


_STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co", "connecticut": "ct",
           "delaware": "de", "district of columbia": "dc", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il",
           "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
           "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo", "montana": "mt",
           "nebraska": "ne", "nevada": "nv", "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
           "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
           "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
           "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy"}


def norm_location(s):
    """'Charlotte, North Carolina 28202' and 'charlotte, NC' -> 'charlotte nc'. Empty when unknown or only 'remote'/'US'."""
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b\d{5}(-\d{4})?\b", " ", s)
    for name in sorted(_STATES, key=len, reverse=True):  # longest first: west virginia before virginia
        s = re.sub(rf"\b{name}\b", _STATES[name], s)
    s = re.sub(r"\b(remote|united states|usa|us|hybrid|on[- ]?site|in[- ]office|area|metro)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def row_key(company, role, location):
    """Dedupe key for tracker rows: normalized company + role + location."""
    return f"{norm_company(company)}|{norm_title(role)}|{norm_location(location)}"


class TrackedIndex:
    """'Is this posting already a tracker row?' Same company and role, and the same location; when either location is
    unknown it counts as the same row (so 'Malvern, PA' and a blank location never become two rows)."""
    def __init__(self, rows=()):
        self.locs = {}
        for r in rows:
            self.add(r.get("company"), r.get("role") or r.get("title"), r.get("location"))

    def add(self, company, role, location):
        self.locs.setdefault(dedupe_key(company, role), set()).add(norm_location(location))

    def has(self, company, role, location):
        seen = self.locs.get(dedupe_key(company, role))
        if not seen:
            return False
        loc = norm_location(location)
        return not loc or "" in seen or loc in seen


def norm_url(u):
    u = (u or "").strip()
    if not u:
        return ""
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.I)
    u = u.split("#")[0]
    # drop tracking params but keep ids like gh_jid / jk
    if "?" in u:
        base, q = u.split("?", 1)
        keep = [kv for kv in q.split("&") if kv.split("=")[0].lower() in {"gh_jid", "jk", "jobid", "job_id", "id", "req", "reqid"}]
        u = base + ("?" + "&".join(sorted(keep)) if keep else "")
    return u.rstrip("/").lower()


def slug(*parts, maxlen=60):
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen].rstrip("-")


def row_id(company, role, date=None):
    return slug(company, role) + "-" + (date or today()).replace("-", "")
