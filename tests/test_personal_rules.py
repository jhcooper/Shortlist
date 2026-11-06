"""Rules that used to be hard-coded for one person now come from config: never-terms, far places, prompt rule text,
and the by-hand verdict names in seen_postings (old names still honoured).
Run: python3 tests/test_personal_rules.py  (exit 1 on any failure). Uses only config.example/ and temp copies of it."""
import os, sys, json, shutil, tempfile, importlib, re, glob
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EX = os.path.join(ROOT, "config.example")
os.environ["CONFIG_DIR"] = EX
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yaml  # noqa: E402
import config  # noqa: E402
import common  # noqa: E402
import prefilter as P  # noqa: E402

fails, n = [], 0


def eq(name, got, want):
    global n
    n += 1
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


def case(**kw):
    base = {"company": "Acme", "title": "Data Analyst", "location": "Philadelphia, PA", "pay": "$95,000", "work_mode": "Hybrid"}
    base.update(kw)
    c = P.normalize(base)
    return c, P.rules(c)[0]


def copy_config(edit):
    """A temp copy of config.example with preferences.yaml changed by edit(prefs)."""
    d = tempfile.mkdtemp()
    shutil.copytree(EX, d, dirs_exist_ok=True)
    f = os.path.join(d, "preferences.yaml")
    prefs = yaml.safe_load(open(f, encoding="utf-8"))
    edit(prefs)
    yaml.safe_dump(prefs, open(f, "w", encoding="utf-8"), sort_keys=False, allow_unicode=True)
    return d


def use_config(d):
    """Re-import prefilter against another config folder."""
    global P
    os.environ["CONFIG_DIR"] = d
    P = importlib.reload(P)


# ---------------------------------------------------------------- never-terms (employers.never_terms)
terms = config.get("preferences.employers.never_terms")
eq("example config has never_terms", bool(terms), True)
_, r = case(snippet="Join the team that designs our loot box rewards.")
eq("never term in posting text drops", r, "never list")
_, r = case(title="Pay-to-Win Systems Analyst")
eq("never term in title drops (any case)", r, "never list")
_, r = case(company="Loot Box Studios")
eq("never term in company drops", r, "never list")
_, r = case(snippet="We avoid loot boxes entirely.")
eq("never term matches whole words only", r, None)
_, r = case(snippet="A calm analytics team.")
eq("posting without never terms kept", r, None)

# ---------------------------------------------------------------- far places, derived from the user's places
lo = P.location_ok
eq("acceptable city", lo("Philadelphia, PA"), True)
eq("acceptable state code", lo("Pittsburgh, PA"), True)
eq("remote", lo("Remote"), True)
eq("state not on the acceptable list is far", lo("Austin, TX"), False)
eq("state name not on the acceptable list is far", lo("Boise, Idaho"), False)
eq("foreign country is far", lo("Montreal, QC, Canada"), False)
eq("foreign country (UK) is far", lo("Manchester, England, United Kingdom"), False)
eq("bare city from places.far_cities is far", lo("Phoenix"), False)
eq("bare city from places.far_cities, any case", lo("MINNEAPOLIS"), False)
eq("bare city nobody listed is unknown (passes, flagged)", lo("Springfield"), None)
eq("empty location is unknown", lo(""), None)
c, r = case(location="Phoenix")
eq("far city drops Career", "Career: location not on your list" in (r or ""), True)
c, r = case(location="Springfield")
eq("unknown city kept", r, None)
eq("unknown city flagged", "location unknown" in P.rules(c)[1], True)
for loc in ("United States or Canada", "USA / Canada", "US, UK", "New England", "Hybrid - New England", "New England area",
            "Mexico Beach", "China Grove", "Ireland Army Community", "U.S."):
    eq(f"US place with a country word is not foreign: {loc}", lo(loc), None)
for loc in ("London, UK", "Berlin, Germany", "Toronto, ON", "Vancouver, BC", "Mexico", "Berlin, Germany."):
    eq(f"foreign place is far: {loc}", lo(loc), False)
eq("country word not at the end is not foreign", P.foreign("Canada Street, Springfield"), False)
eq("province code needs the comma form", P.foreign("Toronto ON"), False)
eq("remote wins over a far country word", lo("Remote (US) or Montreal, Canada"), True)

# ---------------------------------------------------------------- uncertain vs failed reasons (FAIL_RE wording)
for reason in ("unverified (aggregator only); location not on their list", "aggregator only; location not on your list",
               "could not confirm; not on the candidate's list"):
    eq(f"criteria failure wins: {reason}", P.retryable({"verdict": "skip", "reason": reason}), False)
eq("uncertain reason is retryable", P.retryable({"verdict": "skip", "reason": "unverified (aggregator only)"}), True)

# ---------------------------------------------------------------- configs without the new keys keep generic behaviour
def strip_new(prefs):
    prefs["employers"].pop("never_terms", None)
    prefs["places"].pop("far_cities", None)
    for k in ("families", "skip_families", "internships"):
        prefs["roles"].pop(k, None)


bare = copy_config(strip_new)
try:
    r = __import__("subprocess").run([sys.executable, os.path.join(ROOT, "scripts", "config.py"), "check"],
                                     capture_output=True, text=True, env=dict(os.environ, CONFIG_DIR=bare))
    eq("config without the new keys validates", r.returncode, 0)
    use_config(bare)
    _, r = case(snippet="Join the team that designs our loot box rewards.")
    eq("no never_terms: nothing extra drops", r, None)
    eq("no far_cities: bare city is unknown", P.location_ok("Phoenix"), None)
    eq("no far_cities: other states still far", P.location_ok("Austin, TX"), False)
    eq("no far_cities: other countries still far", P.location_ok("Cork, Ireland"), False)
    vals = config.template_values()
    eq("no never_terms: no never-terms sentence", vals["never_terms_rule"], "")
    eq("no internships rule: points at the goals", "must.internships" in vals["internships_rule"], True)
    eq("no families: points at roles", "`roles`" in vals["role_families_rule"], True)
    dream = config.render(open(os.path.join(ROOT, "prompts", "dream.md"), encoding="utf-8").read(), vals)
    eq("dream.md renders without the new keys", "{{" in dream, False)
finally:
    use_config(EX)
    shutil.rmtree(bare)

# ---------------------------------------------------------------- bad types are reported
bad = copy_config(lambda p: p["employers"].__setitem__("never_terms", "loot box"))
r = __import__("subprocess").run([sys.executable, os.path.join(ROOT, "scripts", "config.py"), "check"],
                                 capture_output=True, text=True, env=dict(os.environ, CONFIG_DIR=bad))
shutil.rmtree(bad)
eq("never_terms must be a list", r.returncode == 1 and "employers.never_terms should be a list" in r.stderr, True)

# ---------------------------------------------------------------- prompt rendering with the new placeholders
vals = config.template_values()
dream = config.render(open(os.path.join(ROOT, "prompts", "dream.md"), encoding="utf-8").read(), vals)
eq("dream.md: role families from config", "actuarial analyst" in dream, True)
eq("dream.md: skip examples from config", "insurance sales, field marketing" in dream, True)
eq("dream.md: never terms from config", "loot box, pay-to-win" in dream, True)
eq("dream.md: internship rule from config", "Internships: Keep internships only if they are paid and at least 20 hours a week." in dream, True)
eq("dream.md: fully rendered", "{{" in dream, False)
judge = config.render(open(os.path.join(ROOT, "prompts", "judge.md"), encoding="utf-8").read(), vals)
eq("judge.md: base is any key of base_resumes.json", '"base": "<a key of base_resumes.json>"' in judge, True)
# no prompt refers to the candidate with gendered pronouns
for f in sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md"))):
    txt = open(f, encoding="utf-8").read()
    eq(f"{os.path.basename(f)}: no gendered pronouns", re.findall(r"\b(?:he|him|his|himself|she|her|hers|herself)\b", txt, re.I), [])

# ---------------------------------------------------------------- by-hand verdicts: new names, old names honoured
# Older state named these verdicts after the person ("<name>_open" / "<name>_closed"); "alex_*" below stands in for
# such a legacy name (Alex Rivera is the fictional example person) to prove existing state still works.
import sync_tracker as S  # noqa: E402
eq("new open verdict", S.is_user_verdict("user_open"), True)
eq("new closed verdict", S.is_user_verdict("user_closed"), True)
eq("old person-named open verdict", S.is_user_verdict("alex_open"), True)
eq("old person-named closed verdict", S.is_user_verdict("alex_closed"), True)
for v in ("near_miss", "skip", "filtered", "picked", "closed", "check_yourself", "", None):
    eq(f"not a by-hand verdict: {v!r}", S.is_user_verdict(v), False)

tmp = tempfile.mkdtemp()
old_out, old_state = common.OUT, os.environ.get("STATE_DIR")
try:
    common.OUT = os.path.join(tmp, "out")
    os.environ["STATE_DIR"] = os.path.join(tmp, "state")
    rb = os.path.join(tmp, "readback")
    os.makedirs(os.path.join(rb, "apps"))
    os.makedirs(os.path.join(rb, "checks"))
    answers = [("Old Open Co", "Analyst", "open"), ("Old Closed Co", "Analyst", "closed"),
               ("New Open Co", "Analyst", "open"), ("New Closed Co", "Analyst", "closed")]
    for i, (co, ti, a) in enumerate(answers):
        json.dump({"key": f"k{i}", "company": co, "title": ti, "url": "", "answer": a},
                  open(os.path.join(rb, "checks", f"k{i}.json"), "w"))
    os.makedirs(os.environ["STATE_DIR"])
    json.dump({"version": 1, "postings": [
        {"company": "Old Open Co", "title": "Analyst", "verdict": "alex_open", "reason": "you checked it by hand", "date": "2026-10-01"},
        {"company": "Old Closed Co", "title": "Analyst", "verdict": "alex_closed", "reason": "you checked it by hand", "date": "2026-10-01"},
    ]}, open(os.path.join(os.environ["STATE_DIR"], "seen_postings.json"), "w"))
    listing = os.path.join(tmp, "listing.txt")
    open(listing, "w").write("")
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        S.pull(rb, listing)
    seen = json.load(open(os.path.join(os.environ["STATE_DIR"], "seen_postings.json")))["postings"]
    by_co = {}
    for x in seen:
        by_co.setdefault(x["company"], []).append(x["verdict"])
    eq("old open entry counts as recorded (no duplicate)", by_co["Old Open Co"], ["alex_open"])
    eq("old closed entry counts as recorded (no duplicate)", by_co["Old Closed Co"], ["alex_closed"])
    eq("new open answer recorded as user_open", by_co["New Open Co"], ["user_open"])
    eq("new closed answer recorded as user_closed", by_co["New Closed Co"], ["user_closed"])
    with contextlib.redirect_stdout(io.StringIO()):
        S.pull(rb, listing)
    seen2 = json.load(open(os.path.join(os.environ["STATE_DIR"], "seen_postings.json")))["postings"]
    eq("second pull adds nothing", len(seen2), len(seen))
    # both names block re-evaluation in the prefilter
    _, keys, _ = P.blocking([dict(x, date="2026-11-01") for x in seen2])
    for co in ("Old Open Co", "Old Closed Co", "New Open Co", "New Closed Co"):
        eq(f"prefilter blocks {co}", common.dedupe_key(co, "Analyst") in keys, True)
finally:
    common.OUT = old_out
    if old_state is None:
        os.environ.pop("STATE_DIR", None)
    else:
        os.environ["STATE_DIR"] = old_state
    shutil.rmtree(tmp)

print(f"{n - len(fails)}/{n} passed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
