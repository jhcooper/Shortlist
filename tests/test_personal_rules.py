"""Rules that used to be hard-coded for one person now come from config: never-terms, far places, prompt rule text,
and the by-hand verdict names in seen_postings (old names still honoured).
Uses only config.example/ and temp copies of it."""
import contextlib
import glob
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

import sync_tracker as S
from shortlist import common, config
from shortlist.core import prefilter as P

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EX = os.path.join(ROOT, "config.example")
PROMPTS = sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md")))


def use_config(d):
    """Re-import prefilter against another config folder (it reads config at import; task 2.3 changes that)."""
    os.environ["CONFIG_DIR"] = d
    importlib.reload(P)


@pytest.fixture(scope="module", autouse=True)
def example_config():
    """Every check here is about config.example, whatever CONFIG_DIR the run started with."""
    before = os.environ.get("CONFIG_DIR")
    use_config(EX)
    yield
    if before is None:
        os.environ.pop("CONFIG_DIR", None)
        importlib.reload(P)
    else:
        use_config(before)


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


def check_config(d):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "config.py"), "check"],
                          capture_output=True, text=True, env=dict(os.environ, CONFIG_DIR=d))


# ---------------------------------------------------------------- never-terms (employers.never_terms)
def test_example_config_has_never_terms():
    assert bool(config.get("preferences.employers.never_terms")) is True


@pytest.mark.parametrize(("kw", "want"), [
    ({"snippet": "Join the team that designs our loot box rewards."}, "never list"),
    ({"title": "Pay-to-Win Systems Analyst"}, "never list"),
    ({"company": "Loot Box Studios"}, "never list"),
    ({"snippet": "We avoid loot boxes entirely."}, None),
    ({"snippet": "A calm analytics team."}, None),
], ids=["never term in posting text drops", "never term in title drops (any case)", "never term in company drops",
        "never term matches whole words only", "posting without never terms kept"])
def test_never_terms(kw, want):
    assert case(**kw)[1] == want


# ---------------------------------------------------------------- far places, derived from the user's places
LOCATIONS = [
    ("acceptable city", "Philadelphia, PA", True),
    ("acceptable state code", "Pittsburgh, PA", True),
    ("remote", "Remote", True),
    ("state not on the acceptable list is far", "Austin, TX", False),
    ("state name not on the acceptable list is far", "Boise, Idaho", False),
    ("foreign country is far", "Montreal, QC, Canada", False),
    ("foreign country (UK) is far", "Manchester, England, United Kingdom", False),
    ("bare city from places.far_cities is far", "Phoenix", False),
    ("bare city from places.far_cities, any case", "MINNEAPOLIS", False),
    ("bare city nobody listed is unknown (passes, flagged)", "Springfield", None),
    ("empty location is unknown", "", None),
    *[(f"US place with a country word is not foreign: {loc}", loc, None)
      for loc in ("United States or Canada", "USA / Canada", "US, UK", "New England", "Hybrid - New England", "New England area",
                  "Mexico Beach", "China Grove", "Ireland Army Community", "U.S.")],
    *[(f"foreign place is far: {loc}", loc, False)
      for loc in ("London, UK", "Berlin, Germany", "Toronto, ON", "Vancouver, BC", "Mexico", "Berlin, Germany.")],
    ("remote wins over a far country word", "Remote (US) or Montreal, Canada", True),
]


@pytest.mark.parametrize(("loc", "want"), [(loc, w) for _, loc, w in LOCATIONS], ids=[n for n, _, _ in LOCATIONS])
def test_location_ok(loc, want):
    assert P.location_ok(loc) == want


def test_far_city_drops_career():
    assert ("Career: location not on your list" in (case(location="Phoenix")[1] or "")) is True


def test_unknown_city_kept():
    assert case(location="Springfield")[1] is None


def test_unknown_city_flagged():
    c, _ = case(location="Springfield")
    assert ("location unknown" in P.rules(c)[1]) is True


@pytest.mark.parametrize(("loc", "want"), [("Canada Street, Springfield", False), ("Toronto ON", False)],
                         ids=["country word not at the end is not foreign", "province code needs the comma form"])
def test_foreign(loc, want):
    assert P.foreign(loc) == want


# ---------------------------------------------------------------- uncertain vs failed reasons (FAIL_RE wording)
RETRY = [*[(f"criteria failure wins: {r}", r, False) for r in ("unverified (aggregator only); location not on their list",
                                                                 "aggregator only; location not on your list",
                                                                 "could not confirm; not on the candidate's list")],
         ("uncertain reason is retryable", "unverified (aggregator only)", True)]


@pytest.mark.parametrize(("reason", "want"), [(r, w) for _, r, w in RETRY], ids=[n for n, _, _ in RETRY])
def test_retryable(reason, want):
    assert P.retryable({"verdict": "skip", "reason": reason}) == want


# ---------------------------------------------------------------- configs without the new keys keep generic behaviour
def strip_new(prefs):
    prefs["employers"].pop("never_terms", None)
    prefs["places"].pop("far_cities", None)
    for k in ("families", "skip_families", "internships"):
        prefs["roles"].pop(k, None)


@pytest.fixture
def bare():
    """config.example without the new keys, loaded into prefilter; switched back to the example afterwards."""
    d = copy_config(strip_new)
    use_config(d)
    try:
        yield {"dir": d, "vals": config.template_values()}
    finally:
        use_config(EX)
        shutil.rmtree(d)


def test_config_without_the_new_keys_validates(bare):
    assert check_config(bare["dir"]).returncode == 0


def test_no_never_terms_nothing_extra_drops(bare):
    assert case(snippet="Join the team that designs our loot box rewards.")[1] is None


@pytest.mark.parametrize(("loc", "want"), [("Phoenix", None), ("Austin, TX", False), ("Cork, Ireland", False)],
                         ids=["no far_cities: bare city is unknown", "no far_cities: other states still far",
                              "no far_cities: other countries still far"])
def test_bare_config_locations(bare, loc, want):
    assert P.location_ok(loc) == want


def test_no_never_terms_no_never_terms_sentence(bare):
    assert bare["vals"]["never_terms_rule"] == ""


def test_no_internships_rule_points_at_the_goals(bare):
    assert ("must.internships" in bare["vals"]["internships_rule"]) is True


def test_no_families_points_at_roles(bare):
    assert ("`roles`" in bare["vals"]["role_families_rule"]) is True


def test_dream_renders_without_the_new_keys(bare):
    dream = config.render(open(os.path.join(ROOT, "prompts", "dream.md"), encoding="utf-8").read(), bare["vals"])
    assert ("{{" in dream) is False


# ---------------------------------------------------------------- bad types are reported
def test_never_terms_must_be_a_list():
    bad = copy_config(lambda p: p["employers"].__setitem__("never_terms", "loot box"))
    try:
        r = check_config(bad)
    finally:
        shutil.rmtree(bad)
    assert (r.returncode == 1 and "employers.never_terms should be a list" in r.stderr) is True


# ---------------------------------------------------------------- prompt rendering with the new placeholders
@pytest.fixture
def dream():
    return config.render(open(os.path.join(ROOT, "prompts", "dream.md"), encoding="utf-8").read(), config.template_values())


@pytest.mark.parametrize(("text", "present"), [
    ("actuarial analyst", True),
    ("insurance sales, field marketing", True),
    ("loot box, pay-to-win", True),
    ("Internships: Keep internships only if they are paid and at least 20 hours a week.", True),
    ("{{", False),
], ids=["dream.md: role families from config", "dream.md: skip examples from config", "dream.md: never terms from config",
        "dream.md: internship rule from config", "dream.md: fully rendered"])
def test_dream_prompt(dream, text, present):
    assert (text in dream) is present


def test_judge_base_is_any_key_of_base_resumes():
    judge = config.render(open(os.path.join(ROOT, "prompts", "judge.md"), encoding="utf-8").read(), config.template_values())
    assert ('"base": "<a key of base_resumes.json>"' in judge) is True


# no prompt refers to the candidate with gendered pronouns
@pytest.mark.parametrize("f", PROMPTS, ids=[f"{os.path.basename(f)}: no gendered pronouns" for f in PROMPTS])
def test_no_gendered_pronouns(f):
    txt = open(f, encoding="utf-8").read()
    assert re.findall(r"\b(?:he|him|his|himself|she|her|hers|herself)\b", txt, re.I) == []


# ---------------------------------------------------------------- by-hand verdicts: new names, old names honoured
# Older state named these verdicts after the person ("<name>_open" / "<name>_closed"); "alex_*" below stands in for
# such a legacy name (Alex Rivera is the fictional example person) to prove existing state still works.
@pytest.mark.parametrize(("v", "want"), [
    ("user_open", True), ("user_closed", True), ("alex_open", True), ("alex_closed", True),
    *[(v, False) for v in ("near_miss", "skip", "filtered", "picked", "closed", "check_yourself", "", None)],
], ids=["new open verdict", "new closed verdict", "old person-named open verdict", "old person-named closed verdict",
        *[f"not a by-hand verdict: {v!r}" for v in ("near_miss", "skip", "filtered", "picked", "closed", "check_yourself", "", None)]])
def test_is_user_verdict(v, want):
    assert S.is_user_verdict(v) == want


@pytest.fixture(scope="module")
def pulled():
    """Two tracker pulls into a temp state folder holding old person-named verdicts; returns what was recorded."""
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
        with contextlib.redirect_stdout(io.StringIO()):
            S.pull(rb, listing)
        seen = json.load(open(os.path.join(os.environ["STATE_DIR"], "seen_postings.json")))["postings"]
        with contextlib.redirect_stdout(io.StringIO()):
            S.pull(rb, listing)
        seen2 = json.load(open(os.path.join(os.environ["STATE_DIR"], "seen_postings.json")))["postings"]
        by_co = {}
        for x in seen:
            by_co.setdefault(x["company"], []).append(x["verdict"])
        yield {"seen": seen, "seen2": seen2, "by_co": by_co}
    finally:
        common.OUT = old_out
        if old_state is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = old_state
        shutil.rmtree(tmp)


@pytest.mark.parametrize(("co", "want"), [
    ("Old Open Co", ["alex_open"]), ("Old Closed Co", ["alex_closed"]), ("New Open Co", ["user_open"]), ("New Closed Co", ["user_closed"]),
], ids=["old open entry counts as recorded (no duplicate)", "old closed entry counts as recorded (no duplicate)",
        "new open answer recorded as user_open", "new closed answer recorded as user_closed"])
def test_pull_records_verdicts(pulled, co, want):
    assert pulled["by_co"][co] == want


def test_second_pull_adds_nothing(pulled):
    assert len(pulled["seen2"]) == len(pulled["seen"])


# both names block re-evaluation in the prefilter
@pytest.mark.parametrize("co", ["Old Open Co", "Old Closed Co", "New Open Co", "New Closed Co"], ids=lambda co: f"prefilter blocks {co}")
def test_prefilter_blocks(pulled, co):
    _, keys, _ = P.blocking([dict(x, date="2026-11-01") for x in pulled["seen2"]])
    assert (common.dedupe_key(co, "Analyst") in keys) is True
