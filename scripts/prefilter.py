"""Normalize -> dedupe -> hard filter -> pre-score. Zero model cost.

  python3 scripts/prefilter.py
      reads   out/raw/*.jsonl  (one JSON object per line, written by the gather steps)
      writes  out/shortlist.json (survivors, best pre-score first, with flags for unknowns)
              out/dropped.json   (dropped candidates with the rule that dropped them)
      appends dropped + duplicate postings to data/seen_postings.json

Raw candidate fields (all optional except company + title):
  company, title, location, url, source, pay (free text), remote (bool), work_mode,
  posted (YYYY-MM-DD) or days_ago, employment_type, snippet (any posting text),
  goal_hint (a goal id from preferences.yaml "goals", e.g. "career" | "bridge"; legacy track_hint
  "short"/"long" still works), dream (bool)

One pipeline for every goal: shared exclusions first, then the job is checked against each goal and labeled
with every goal it fits (c["goals"], best guess first). It is dropped only if it fits no goal.
Every rule drops only on explicit evidence. Missing information passes, flagged.

Re-looks: postings set aside for uncertainty (seen entries with retry true: blocked page, aggregator only,
not verified) come back after RELOOK_DAYS, at most MAX_RELOOKS times (dream employers: no limit).
"""
import re, glob, os
from common import p, outp, load, save, load_jsonl, today, norm_url, norm_company, dedupe_key, now_et, TrackedIndex, dp
import datetime as dt

from config import load_config
from common import _STATES

_CFG = load_config()                       # CONFIG_DIR: preferences.yaml + sources.yaml (validated)
CRIT = _CFG["preferences"]
_PLACES, _ROLES = CRIT["places"], CRIT["roles"]
DREAM = {norm_company(e["name"]) for e in _CFG["sources"]["dream_employers"]}
NEVER = [n.lower() for n in CRIT["employers"]["never_names"]]
NEVER_FLAT = {re.sub(r"[^a-z]", "", n) for n in NEVER}  # "Data Annotation" and "DataAnnotation" are the same company
NEVER_TERMS_LIST = [t for t in (CRIT["employers"].get("never_terms") or []) if t.strip()]


def _phrases(terms, bound=r"[a-z0-9]"):
    """Literal phrases from config as one case-insensitive pattern, matched as whole words (never as regex)."""
    alts = "|".join(re.escape(t.lower()) for t in sorted(set(terms), key=len, reverse=True))
    return re.compile(rf"(?<!{bound})({alts})(?!{bound})", re.I)


def _words(terms):
    """Same word-boundary rule the filter always used (\\b...\\b) for title word lists."""
    return re.compile(r"\b(" + "|".join(re.escape(t.lower()) for t in terms) + r")\b", re.I)


# employers.never_terms: words that drop a posting wherever they appear (company, title or posting text), whole words
NEVER_TERMS = _phrases(NEVER_TERMS_LIST) if NEVER_TERMS_LIST else None

COHORT = CRIT["cohort"]["terms"]
COHORT_START = tuple(int(x) for x in CRIT["cohort"]["career_start_on_or_after"].split("-"))
GOALS = CRIT["goals"]
GOAL_IDS = [g["id"] for g in GOALS]
GOAL_BY_ID = {g["id"]: g for g in GOALS}
DEFAULT_GOAL = next((g["id"] for g in GOALS if g.get("default")), GOAL_IDS[0])
HINT_ALIASES = {"short": "bridge", "long": "career", "short_term": "bridge", "long_term": "career"}

OK_STATES = set(_PLACES["acceptable_states"])
OK_CITY = _phrases(_PLACES["acceptable_cities"])
OK_STATE_NAMES = _phrases(_PLACES["acceptable_state_names"])
# every other state name counts as elsewhere ("washington" alone could be DC, so only "washington state" counts)
_FAR = sorted({("washington state" if n == "washington" else n) for n in _STATES} - {x.lower() for x in _PLACES["acceptable_state_names"]})
FAR_STATE_NAMES = _phrases(_FAR)
# "Far" is derived from the user's places: a location is far when it names a US state that is not acceptable (above),
# another country (FOREIGN_COUNTRIES, generic and used only to recognize a place as outside the US), or a city in
# places.far_cities (optional; for bare city names such as "Springfield" with no state). Acceptable phrases win first.
FOREIGN_COUNTRIES = ["canada", "mexico", "united kingdom", "uk", "england", "scotland", "wales", "ireland", "india", "china",
                     "japan", "singapore", "philippines", "australia", "new zealand", "germany", "france", "spain", "italy",
                     "netherlands", "belgium", "switzerland", "sweden", "poland", "portugal", "israel", "brazil", "argentina"]
# A country counts only as the whole location or its last comma-separated part ("Berlin, Germany", "London, UK"), so
# "New England", "Mexico Beach" or "China Grove" are not foreign. A Canadian province code at the end ("Toronto, ON")
# counts too. A location that also names the US ("United States or Canada", "US, UK") is never treated as foreign.
FOREIGN_RE = re.compile(r"(?:^|,)\s*(?:" + "|".join(re.escape(c) for c in sorted(FOREIGN_COUNTRIES, key=len, reverse=True))
                        + r")\s*\.?\s*$", re.I)
CA_PROVINCE_RE = re.compile(r",\s*(?:ON|BC|QC|AB|MB|SK|NS|NB|NL|PE)\s*$")
US_MARKER_RE = re.compile(r"(?<![a-z])(?:us|u\.s\.?|usa|u\.s\.a\.?|united states)(?![a-z])", re.I)


def foreign(loc):
    """True when the location names another country (see FOREIGN_RE) and does not also name the US."""
    return not US_MARKER_RE.search(loc) and bool(FOREIGN_RE.search(loc) or CA_PROVINCE_RE.search(loc))
_FAR_CITY_LIST = [c for c in (_PLACES.get("far_cities") or []) if c.strip()]
FAR_CITIES = _phrases(_FAR_CITY_LIST) if _FAR_CITY_LIST else None
STATE_RE = re.compile(r"(?:,\s*|\b)(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")
REMOTE_RE = re.compile(r"\bremote\b|work from home|\bwfh\b|anywhere in the u\.?s", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)

# --- work mode: only explicit arrangement sentences in free text count ---
TXT_HYBRID = re.compile(r"\bhybrid\b|\b[1-4]\s*(?:-\s*[1-4]\s*)?days?\s*(?:a|per|each)?\s*week\s*(?:in|on)[- ]?(?:the\s+)?(?:office|site)|\b[1-4]\s*days?\s*in[- ](?:the\s+)?office", re.I)
TXT_ONSITE = re.compile(r"(?:fully|100\s*%|entirely)\s*(?:on[- ]?site|in[- ]office|in the office|in[- ]person)"
                        r"|\b(?:5|five)\s*days?\s*(?:a|per)?\s*week\s*(?:in|on)[- ]?(?:the\s+)?(?:office|site)"
                        r"|\b(?:this|the)\s+(?:role|position|job)\s+is\s+(?:an?\s+)?(?:fully\s+|entirely\s+)?(?:on[- ]?site|in[- ]office|in[- ]person)"
                        r"|\bmust\s+(?:work|be|report)\s+(?:on[- ]?site|in[- ]office|in[- ]person|in the office)"
                        r"|\bno remote\b|\bnot (?:a )?remote\b|\bon[- ]?site (?:role|position|job)\b", re.I)
TXT_REMOTE = re.compile(r"\b(?:fully|100\s*%)\s*remote\b|\b(?:this|the)\s+(?:role|position|job)\s+is\s+(?:fully\s+)?remote\b|\bremote[- ]first\b|\bwork from anywhere\b", re.I)

# --- seniority ---
SENIOR_HARD = re.compile(r"\b(senior|sr\.?|staff|principal|director|head of|vp|vice president|chief)\b", re.I)
MANAGER_RE = re.compile(r"\bmanager\b", re.I)
MANAGER_OK = re.compile(r"\b(associate|assistant|junior|jr\.?)\s+(?:\w+\s+)?manager\b", re.I)
LEAD_RE = re.compile(r"\b(team lead|tech lead|technical lead|lead)\b(?![- ]?gen)", re.I)
ROLE_WORDS = r"(?:engineer|analyst|developer|scientist|consultant|specialist|associate|architect|administrator|programmer|designer|technologist|representative)"
LEVEL_HI = re.compile(r"(?:\b" + ROLE_WORDS + r"\s*,?\s*|\blevel\s*|\bL)(iii|iv|v|vi|3|4|5|6)\b|\s(iii|iv)\s*$", re.I)
LEVEL_II = re.compile(r"(?:\b" + ROLE_WORDS + r"\s*,?\s*|\blevel\s*|\bL)(ii|2)\b|\sii\s*$", re.I)
SENIOR_OK = re.compile(r"\b(new grad|graduate program|early career|university|campus|entry[- ]level|junior|rotational|development program|leadership program|analyst program"
                       + "".join("|" + re.escape(t) for t in COHORT) + r")\b", re.I)

# --- experience ---
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(\d{1,2})?\s*\+?\s*years?(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,4}?experience", re.I)
SELF_RE = re.compile(r"\b(we|we've|we have|our|the company|the firm|founded|in business|serving|with over|for over|has over|have over|has been|history of|track record)\b", re.I)
PREFERRED_RE = re.compile(r"prefer|nice[- ]to[- ]have|ideally|a plus|\bbonus\b|desired|desirable|helpful", re.I)
SUBSTITUTE_RE = re.compile(r"\bor\s+(?:a\s+|an\s+)?(?:master|m\.?s\.?\b|graduate degree|advanced degree|ph\.?d|equivalent|relevant degree)", re.I)

CLEAR_RE = re.compile(r"(active|current|ts/sci|top secret|secret|public trust)[^.]{0,40}clearance|clearance[^.]{0,30}(required|must|eligib|preferred|obtain)|(obtain|eligib\w*)[^.]{0,30}clearance", re.I)
PHD_RE = re.compile(r"\bph\.?d\.?\b[^.]{0,30}(required|must)", re.I)
OT_RE = re.compile(r"mandatory overtime|required overtime|overtime (is )?required", re.I)
TRAVEL_RE = re.compile(r"(\d{2,3})\s*%\s*(?:of the time\s*)?travel|travel[^.]{0,20}?(\d{2,3})\s*%", re.I)
_MONTH_ABBR = r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
START_RE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                      + _MONTH_ABBR + r"|summer|fall|spring|winter)\.?\s+(20\d\d)\b", re.I)
MONTH_ABBR_DOT_RE = re.compile(r"\b(" + _MONTH_ABBR + r")\.(?=\s*20\d\d)", re.I)  # "Jan. 2027": this period ends no window
# start / starts / starting / start date / begins / beginning, but not start-up, kick-start, jump-start, head start,
# "start your career", "starting salary", "starting pay" or "starting at". A ":" and line break after it are skipped.
START_PHRASE_RE = re.compile(r"(?<!kick-)(?<!jump-)(?<!head )\b(?:start(?:s|ing)?\b(?![-\s]?up\b)(?!\s+(?:your|salary|pay|at)\b)"
                             r"(?:\s+date\b)?|begins|beginning)\b(?:\s*:\s*)?", re.I)
START_WINDOW = 40  # characters after a start phrase in which a month and year count as the start date
START_WINDOW_END_RE = re.compile(r"[.!?;\n]")  # the window never crosses a sentence or clause boundary
# A date directly before the phrase, with only spaces or a hyphen between: "Summer 2026 start", "Fall 2026 - cohort start"
START_BEFORE_RE = re.compile(START_RE.pattern + r"\s*-?\s*(?:cohort\s+)?start\b(?!\s+your\b)", re.I)
OFF_TARGET_RE = _words(_ROLES["off_target_title_words"])
OFF_TARGET_UNLESS = _words(_ROLES["off_target_unless"])
INTERN_RE = re.compile(r"\bintern(ship)?\b", re.I)

# --- goal evidence (structured fields and title only; description words never decide) ---
GIG_TYPE_RE = re.compile(r"part[- ]time|temporary|\btemp\b|freelance|contract|contractor|per diem|seasonal", re.I)
C2H_RE = re.compile(r"contract[- ]to[- ](?:hire|perm)|temp[- ]to[- ](?:hire|perm)|\bc2h\b|right[- ]to[- ]hire", re.I)
GIG_TITLE_RE = re.compile(r"\b(ai trainer|ai training|annotat\w*|evaluator|rater|rlhf|labell?ing|tutor|expert contributor)\b", re.I)
FLEX_RE = re.compile(r"flexible hours|set your own (?:hours|schedule)|work when you want|part[- ]time|hours per week|hrs/week|hrs per week", re.I)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS.update({"sept": 9, "spring": 4, "summer": 6, "fall": 9, "winter": 1})


# Retirement plans are not pay: 401k / 401(k), 403b / 403(b), 457 / 457(b).
RETIREMENT_RE = re.compile(r"(?<![$\d.])\b(?:401\s*\(?\s*k|403\s*\(?\s*b|457(?:\s*\(?\s*b)?)\b\s*\)?")
PAY_NUM_RE = re.compile(r"((?:us)?\$|\busd\b)?\s*(\d+(?:\.\d+)?)\s*(k(?![a-z]))?")  # a leading usd / us$ counts like $
PAY_UNIT_AFTER_RE = re.compile(r"\s*(?:/\s*(?:h(?:ou)?r|yr|year|annum)|per\s+(?:hour|hr|year|yr|annum)|an?\s+(?:hour|year)"
                               r"|hourly|annually|yearly|usd|dollars?)\b")
PAY_LABEL_BEFORE_RE = re.compile(r"\b(?:salary|pay|compensation)\s*:?\s*$")  # "Salary: 95000" (only for 1,000 or more)
PAY_RANGE_SEP_RE = re.compile(r"\s*(?:-|\u2013|\u2014|to)\s*")


def parse_pay(text):
    """-> (min, max, unit) annual or hourly, or (None, None, None).
    A number counts only with a $ (or usd / us$), a k suffix, a pay unit right after it, a salary/pay/compensation
    label right before it (1,000 or more), or as the other end of a range with one. Retirement plans (401k, 403b, 457)
    and bare numbers like "15 days PTO" or an unlabeled "45000-50000" are ignored. In "$85-100k" the k applies to both ends."""
    if not text:
        return None, None, None
    t = RETIREMENT_RE.sub(" ", str(text).lower().replace(",", ""))
    hourly = bool(re.search(r"/\s*h(ou)?r|per hour|hourly|an hour", t))
    found = []  # [value, qualifies, start, end, has_k]
    for m in PAY_NUM_RE.finditer(t):
        v = float(m.group(2))
        if m.group(3):
            v *= 1000
        ok = bool(m.group(1) or m.group(3) or PAY_UNIT_AFTER_RE.match(t, m.end())
                  or (v >= 1000 and PAY_LABEL_BEFORE_RE.search(t, 0, m.start(2))))
        found.append([v, ok, m.start(), m.end(), bool(m.group(3))])
    for a, b in zip(found, found[1:]):  # "40-45 per hour", "$85-100k": both ends of a range count
        sep = PAY_RANGE_SEP_RE.fullmatch(t, a[3], b[2])
        if sep and (a[1] or b[1]):
            a[1] = b[1] = True
            if b[4] and not a[4] and a[0] < 1000 <= b[0]:  # "$85-100k" -> 85,000-100,000
                a[0] *= 1000
    nums = [f[0] for f in found if f[1] and f[0] >= 5]
    if not nums:
        return None, None, None
    if not hourly and max(nums) < 300:
        hourly = True
    nums = nums[:2]
    return min(nums), max(nums), "hour" if hourly else "year"


def work_mode(c, text=None):
    """Structured field first, then title/location, then only explicit arrangement sentences."""
    wm = (c.get("work_mode") or "").lower()
    if c.get("remote") is True or "remote" in wm:
        return "remote"
    if "hybrid" in wm:
        return "hybrid"
    if re.search(r"on-?site|in[- ]?office|in[- ]person|physical", wm):
        return "on-site"
    tl = f"{c.get('title') or ''} {c.get('location') or ''}"
    if HYBRID_RE.search(tl):
        return "hybrid"
    if REMOTE_RE.search(tl):
        return "remote"
    if re.search(r"\(on-?site\)|\bon-?site\b", tl, re.I):
        return "on-site"
    snip = c.get("snippet") or ""
    if TXT_HYBRID.search(snip):
        return "hybrid"
    if TXT_ONSITE.search(snip):
        return "on-site"
    if TXT_REMOTE.search(snip):
        return "remote"
    return ""


def location_ok(loc):
    """True / False / None(unknown)."""
    if not loc:
        return None
    if REMOTE_RE.search(loc) or OK_CITY.search(loc):
        return True
    if OK_STATE_NAMES.search(loc) and not re.search(r"\bwest virginia\b", loc, re.I):
        return True
    if FAR_STATE_NAMES.search(loc):
        return False
    states = set(STATE_RE.findall(loc))
    if states & OK_STATES:
        return True
    if states or foreign(loc) or (FAR_CITIES and FAR_CITIES.search(loc)):
        return False
    return None


def hint_goal(h):
    h = (h or "").strip().lower()
    h = HINT_ALIASES.get(h, h)
    return h if h in GOAL_BY_ID else None


def normalize(c):
    lo, hi, unit = parse_pay(c.get("pay"))
    days = c.get("days_ago")
    if days is None and c.get("posted"):
        try:
            days = (now_et().date() - dt.date.fromisoformat(str(c["posted"])[:10])).days
        except ValueError:
            days = None
    return {
        "company": (c.get("company") or "").strip(), "title": (c.get("title") or "").strip(),
        "location": (c.get("location") or "").strip(), "url": (c.get("url") or "").strip(),
        "source": c.get("source") or "", "pay_text": c.get("pay") or "", "pay_min": lo, "pay_max": hi, "pay_unit": unit,
        "work_mode": work_mode(c), "days_ago": days, "employment_type": c.get("employment_type") or "",
        "snippet": (c.get("snippet") or "")[:1500], "hint": hint_goal(c.get("goal_hint") or c.get("track_hint")),
        "dream": bool(c.get("dream")) or norm_company(c.get("company")) in DREAM,
    }


def years_check(snippet):
    """-> (drop_reason or None, flags). Only applicant-directed requirements count."""
    flags = []
    for sent in re.split(r"(?<=[.;!?])\s+|\n|•|·|•", snippet or ""):
        for m in YEARS_RE.finditer(sent):
            n = int(m.group(1))
            if n > 15 or n == 0:
                continue  # company history, not a requirement
            before = sent[max(0, m.start() - 60):m.start()]
            if SELF_RE.search(before) or re.search(r"serving|in the industry|of combined", sent[m.end():m.end() + 30], re.I):
                continue
            if PREFERRED_RE.search(sent):
                if n >= 3:
                    flags.append(f"{n} yrs preferred")
                continue
            if SUBSTITUTE_RE.search(sent):
                if n >= 3:
                    flags.append("years or degree")
                continue
            if n >= CRIT["shared_exclusions"]["experience_years_required_drop_at"]:
                return f"needs {n}+ years", flags
            if n >= 3:
                flags.append(f"asks {n} yrs")
    return None, sorted(set(flags))


def shared(c):
    """Exclusions that apply to every goal. -> (drop_reason or None, flags)."""
    t, s = c["title"], c["snippet"]
    allt = f"{t} {s}"
    co = c["company"].lower()
    flags = []
    co_flat = re.sub(r"[^a-z]", "", co)
    if any(n in co for n in NEVER) or any(n and n in co_flat for n in NEVER_FLAT) or (NEVER_TERMS and NEVER_TERMS.search(f"{co} {allt}")):
        return "never list", flags
    if not SENIOR_OK.search(t):
        if SENIOR_HARD.search(t):
            return "senior title", flags
        if MANAGER_RE.search(t) and not MANAGER_OK.search(t):
            return "manager title", flags
        if LEAD_RE.search(t):
            return "lead title", flags
        if LEVEL_HI.search(t):
            return "level 3+ title", flags
    if LEVEL_II.search(t):
        flags.append("level II")
    if OFF_TARGET_RE.search(t) and not OFF_TARGET_UNLESS.search(t):
        return "off-target role", flags
    r, f = years_check(s)
    flags += f
    if r:
        return r, flags
    if CLEAR_RE.search(allt):
        return "security clearance", flags
    if PHD_RE.search(allt):
        return "PhD required", flags
    if OT_RE.search(allt):
        return "mandatory overtime listed", flags
    m = TRAVEL_RE.search(allt)
    if m:
        pct = int(m.group(1) or m.group(2))
        if pct > CRIT["shared_exclusions"]["travel_over_percent"]:
            return f"travel {pct}%", flags
    if c["days_ago"] is not None and c["days_ago"] > CRIT["shared_exclusions"]["posted_over_days"] and not c["dream"]:
        return f"posted {c['days_ago']} days ago", flags
    return None, flags


def annual(c, v):
    return v * 2080 if c["pay_unit"] == "hour" else v


def hourly(c, v):
    return v if c["pay_unit"] == "hour" else v / 2080


def start_dates(text):
    """-> [(month, year)] that read as a start date: within START_WINDOW characters after a start phrase and in the same
    sentence or clause ("start date: June 2026", "Start: Jan. 2027", "starting September 2026", "program begins July 2026"),
    or directly before "start" / "cohort start" ("Summer 2026 start"). A month and year elsewhere ("Posted October 2026")
    is not a start, and neither is one in a year before the current one ("founded March 2019")."""
    out = [m.groups()[:2] for m in START_BEFORE_RE.finditer(text)]
    for p in START_PHRASE_RE.finditer(text):
        win = MONTH_ABBR_DOT_RE.sub(r"\1 ", text[p.end():p.end() + START_WINDOW + 20])
        win = START_WINDOW_END_RE.split(win, 1)[0]
        for m in START_RE.finditer(win):
            if m.start() <= START_WINDOW:
                out.append(m.groups())
    year = now_et().year
    return [(mon, yr) for mon, yr in out if int(yr) >= year]


def fit_career(c):
    """-> reason it fails the Career goal, or None."""
    t, s = c["title"], c["snippet"]
    allt = f"{t} {s}"
    if c["work_mode"] == "on-site":
        return "fully in-person"
    if GIG_TITLE_RE.search(t):
        return "AI-training/tutoring gig"
    if INTERN_RE.search(t) and not re.search("|".join([*map(re.escape, COHORT), "new grad", "graduate program"]), allt, re.I):
        return "internship"
    if c["pay_max"] is not None:
        a = annual(c, c["pay_max"])
        if a < GOAL_BY_ID["career"]["must"]["pay_floor_annual_usd"]:
            return f"pay tops out at ${int(a):,}"
    for mon, yr in start_dates(allt):
        if (int(yr), MONTHS[mon.lower()]) < COHORT_START:
            return f"starts {mon} {yr}"
    if location_ok(c["location"]) is False and c["work_mode"] != "remote":
        return "location not on your list"
    return None


def fit_bridge(c):
    """-> reason it fails the Bridge goal, or None."""
    t = c["title"]
    evidence = (c["hint"] == "bridge" or GIG_TYPE_RE.search(c["employment_type"] or "") or GIG_TYPE_RE.search(t)
                or GIG_TITLE_RE.search(t) or FLEX_RE.search(c["snippet"]))
    if not evidence:
        return "no sign of part-time, contract or flexible hours"
    if C2H_RE.search(f"{t} {c['snippet']}") and c["hint"] != "bridge":
        return "contract-to-hire (Career)"
    if c["work_mode"] and c["work_mode"] != "remote":
        return "not remote"
    if c["pay_max"] is not None:
        h = hourly(c, c["pay_max"])
        if h < GOAL_BY_ID["bridge"]["must"]["pay_floor_hourly_usd"]:
            return f"under ${GOAL_BY_ID['bridge']['must']['pay_floor_hourly_usd']}/hr"
    return None


FITS = {"career": fit_career, "bridge": fit_bridge}


def rules(c):
    """Shared exclusions, then every goal. Sets c['goals'] (best guess first) and c['track'].
    -> (drop_reason or None, flags)."""
    reason, flags = shared(c)
    if reason:
        return reason, flags
    order = ([c["hint"]] if c["hint"] else []) + [g for g in GOAL_IDS if g != c["hint"]]
    ok, why = [], {}
    for g in order:
        fn = FITS.get(g)
        r = fn(c) if fn else None
        if r:
            why[g] = r
        else:
            ok.append(g)
    if not ok:
        return "; ".join(f"{GOAL_BY_ID[g]['name']}: {why[g]}" for g in order), flags
    c["goals"] = ok
    c["track"] = "short" if ok[0] == "bridge" else "long"  # legacy field for older readers
    et = c["employment_type"] or ""
    if "career" in ok and (GIG_TYPE_RE.search(et) or re.search(r"\bcontract\b", c["title"], re.I)):
        flags.append("contract role")
    if C2H_RE.search(f"{c['title']} {c['snippet']}"):
        flags.append("contract-to-hire")
    lo = location_ok(c["location"])
    if lo is None and "career" in ok:
        flags.append("location unknown")
    if not c["work_mode"]:
        flags.append("work mode unknown")
    if c["pay_max"] is None:
        flags.append("pay not listed")
    return None, sorted(set(flags))


def prescore(c):
    s = 0.0
    loc = c["location"].lower()
    g0 = c["goals"][0]
    if c["dream"]:
        s += 2
    for band in _PLACES["score"]:  # first matching band wins (plain substring match, as always)
        if any(t.lower() in loc for t in band["contains"]):
            s += band["points"]
            break
    if c["work_mode"] == "hybrid":
        s += 1.2
    elif c["work_mode"] == "remote":
        s += 1.5 if g0 == "bridge" else 0.6
    t = c["title"].lower()
    love, ok = _ROLES["title_keywords"]["love"], _ROLES["title_keywords"]["ok"]
    if g0 == "bridge" and GIG_TITLE_RE.search(t):
        s += 1.5
    elif any(k in t for k in love):
        s += 1.5
    elif any(k in t for k in ok):
        s += 1
    if c["pay_max"] is not None:
        s += 1 if annual(c, c["pay_max"]) >= CRIT["scoring"]["prescore_pay_good_annual"] else 0.3
    if c["days_ago"] is not None and c["days_ago"] <= 7:
        s += 0.3
    if len(c["goals"]) > 1:
        s += 0.2
    if c["days_ago"] is None and re.search(r"alert|indeed|glassdoor|built ?in|jobright|linkedin", c["source"], re.I):
        s -= 0.6  # undated alert/aggregator listings are often months old: verify them only if there is room
    return round(s, 2)


RELOOK_DAYS = 5
MAX_RELOOKS = 2
IGNORED_VERDICTS = {"filtered_old_rules"}  # drops made by rules that were later found to be wrong
UNCERTAIN_RE = re.compile(r"not verified|unverified|could not|couldn't|cannot|can't|blocked|would not load|wouldn't load|did not load|"
                          r"not confirmed|aggregator|beyond verify|not readable|no details|only on|only the|page (?:was )?empty|"
                          r"redirects to the generic|no matching posting", re.I)


FAIL_RE = re.compile(r"on-?site|in-office|in-person|dealbreaker|ruled out|never list|defense|military|surveillance|"
                     r"below the|under the floor|strong negative|\bclosed\b|expired|stale|internship|not on (?:[\w']+\s+){0,2}list|"
                     r"phd|under-200|startup", re.I)


def retryable(x):
    if x.get("verdict") not in ("skip", "near_miss"):
        return False
    if "retry" in x:
        return bool(x["retry"])
    r = x.get("reason") or ""  # entries written before the retry flag existed: a stated criteria failure wins
    return bool(UNCERTAIN_RE.search(r)) and not FAIL_RE.search(r)


def blocking(postings):
    """-> (urls, keys) that must not be evaluated again today, and {key: looks} for re-looks."""
    today_d = dt.date.fromisoformat(today())
    by_key = {}
    for x in postings:
        if x.get("verdict") in IGNORED_VERDICTS:
            continue
        by_key.setdefault(dedupe_key(x["company"], x["title"]), []).append(x)
    urls, keys, looks = set(), set(), {}
    for k, xs in by_key.items():
        final = [x for x in xs if not retryable(x)]
        unc = [x for x in xs if retryable(x)]
        block = bool(final)
        if not block and unc:
            last = max(x.get("date", "") for x in unc)
            age = (today_d - dt.date.fromisoformat(last)).days if last else 99
            dream = norm_company(unc[-1].get("company")) in DREAM
            if age < RELOOK_DAYS or (len(unc) > MAX_RELOOKS and not dream):
                block = True
            else:
                looks[k] = len(unc)
        if block:
            keys.add(k)
            urls |= {norm_url(x.get("url")) for x in xs if x.get("url")}
    return urls, keys, looks


def main():
    raw = []
    for f in sorted(glob.glob(outp("raw", "*.jsonl"))):
        raw.extend(load_jsonl(f))
    tracker = load(dp("tracker.json"))["rows"]
    seen = load(dp("seen_postings.json"), {"version": 1, "postings": []})
    have_url = {norm_url(r.get("posting_url")) for r in tracker if r.get("posting_url")}
    tracked = TrackedIndex(tracker)  # already a tracker row: company + role + location (unknown location = same row)
    bu, bk, relook = blocking(seen["postings"])
    have_url |= bu
    have_key = set(bk)  # already evaluated (seen postings): company + title

    short, dropped, batch_keys = [], [], set()
    for rc in raw:
        if not rc.get("company") or not rc.get("title"):
            continue
        c = normalize(rc)
        key = dedupe_key(c["company"], c["title"])
        nu = norm_url(c["url"])
        if (nu and nu in have_url) or key in have_key or tracked.has(c["company"], c["title"], c["location"]):
            continue  # already tracked or already evaluated
        if key in batch_keys:
            # same posting from two sources today: keep the one with more data
            prev = next(x for x in short if dedupe_key(x["company"], x["title"]) == key) if any(dedupe_key(x["company"], x["title"]) == key for x in short) else None
            if prev and not prev["url"] and c["url"]:
                prev["url"] = c["url"]
            continue
        batch_keys.add(key)
        if key in relook:
            c["relook"] = relook[key]
        reason, flags = rules(c)
        if c.get("relook"):
            flags = flags + [f"re-look {c['relook']}"]
        if reason:
            c["dropped_because"] = reason
            dropped.append(c)
        else:
            c["flags"] = flags
            c["prescore"] = prescore(c)
            short.append(c)
    short.sort(key=lambda c: (-c["dream"], -c["prescore"]))
    for i, c in enumerate(short, 1):
        c["cid"] = f"c{i:03d}"
    save(outp("shortlist.json"), short)
    save(outp("dropped.json"), dropped)
    for c in dropped:
        seen["postings"].append({"company": c["company"], "title": c["title"], "url": c["url"], "verdict": "filtered", "reason": c["dropped_because"], "date": today()})
    save(dp("seen_postings.json"), seen)
    by_reason = {}
    for c in dropped:
        k = re.sub(r"\d[\d,$+]*", "N", c["dropped_because"].split(";")[0])[:40]
        by_reason[k] = by_reason.get(k, 0) + 1
    by_goal = {g: sum(1 for c in short if g in c["goals"]) for g in GOAL_IDS}
    print(f"raw {len(raw)} -> new {len(short) + len(dropped)} -> shortlist {len(short)} (dream {sum(c['dream'] for c in short)}, "
          f"re-looks {sum(1 for c in short if c.get('relook'))}, by goal {by_goal}), dropped {len(dropped)} {by_reason}")


if __name__ == "__main__":
    main()
