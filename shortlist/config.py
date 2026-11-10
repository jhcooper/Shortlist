"""The only way the engine reads personal settings. Everything about the person lives in CONFIG_DIR:

  profile.yaml       who they are: contact, education, skills, experience (source of truth), resume base labels
  preferences.yaml   what they want: goals, exclusions, places, roles, employers, scoring, digest, integrations
  sources.yaml       where to look: search queries, dream employers, email alert filters
  resumes/base_resumes.json   curated base resumes (each a subset of profile.yaml experience)
  state/             run state the daily run writes (tracker mirror, seen postings, logs, rotation pointers)

CONFIG_DIR comes from the CONFIG_DIR environment variable, else from a one-line `.config_dir` file in the engine root
(the daily run writes it at setup, because each shell starts fresh). STATE_DIR defaults to CONFIG_DIR/state.

  python3 scripts/config.py check            validate everything; exit 1 with readable errors
  python3 scripts/config.py summary          one line per value the run needs (name, recipient, tracker link, ...)
  python3 scripts/config.py get <dotted>     print one value (JSON for lists/dicts)
  python3 scripts/config.py render-prompts   prompts/*.md -> out/prompts/*.md with {{placeholders}} filled
  python3 scripts/config.py state-dir        print the run-state folder
"""
import os
import re
import sys
import json
import glob
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigError(Exception):
    pass


def config_dir():
    d = os.environ.get("CONFIG_DIR", "").strip()
    if not d:
        f = os.path.join(ROOT, ".config_dir")
        if os.path.exists(f):
            d = open(f, encoding="utf-8").read().strip()
    if not d:
        raise ConfigError("CONFIG_DIR is not set. Point it at your config folder (copy config.example/ to start), "
                          "or write that path into a .config_dir file in the engine folder.")
    d = os.path.abspath(os.path.expanduser(d))
    if not os.path.isdir(d):
        raise ConfigError(f"CONFIG_DIR {d} does not exist.")
    return d


def state_dir():
    d = os.environ.get("STATE_DIR", "").strip() or os.path.join(config_dir(), "state")
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(os.path.expanduser(d))


def cpath(*parts):
    return os.path.join(config_dir(), *parts)


def spath(*parts):
    return os.path.join(state_dir(), *parts)


# ---------------------------------------------------------------- schema
# Each rule: dotted path -> type ("str", "int", "num", "list", "dict", "email", "ym" = YYYY-MM, "str[]" = list of str).
# A path ending in "?" is optional. "*" in a path means every item of a list.
SCHEMA = {
    "profile.yaml": [
        ("schema_version", "int"),
        ("person.id", "str"), ("person.name", "str"), ("person.first_name", "str"), ("person.email", "email"),
        ("person.phone?", "str"), ("person.home", "str"), ("person.tagline?", "str"), ("person.title?", "str"),
        ("person.links", "list"), ("person.links.*.label", "str"), ("person.links.*.url", "str"),
        ("education.resume_lines", "list"), ("education.resume_lines.*.line", "str"), ("education.resume_lines.*.date", "str"),
        ("education.graduation", "ym"), ("education.gpa", "dict"), ("education.gpa.resume_shows", "str"),
        ("skills.approved", "str[]"),
        ("experience", "list"), ("experience.*.org", "str"), ("experience.*.title", "str"), ("experience.*.loc", "str"),
        ("experience.*.dates", "str"), ("experience.*.bullets", "str[]"),
        ("resume_bases", "dict"),
    ],
    "preferences.yaml": [
        ("schema_version", "int"),
        ("goals", "list"), ("goals.*.id", "str"), ("goals.*.name", "str"),
        ("shared_exclusions.experience_years_required_drop_at", "int"), ("shared_exclusions.travel_over_percent", "int"),
        ("shared_exclusions.posted_over_days", "int"),
        ("places.acceptable_states", "str[]"), ("places.acceptable_state_names", "str[]"), ("places.acceptable_cities", "str[]"),
        ("places.far_cities?", "str[]"),
        ("places.score", "list"), ("places.score.*.points", "num"), ("places.score.*.contains", "str[]"),
        ("roles.title_keywords.love", "str[]"), ("roles.title_keywords.ok", "str[]"),
        ("roles.off_target_title_words", "str[]"), ("roles.off_target_unless", "str[]"),
        ("roles.families?", "str[]"), ("roles.skip_families?", "str[]"), ("roles.internships?", "str"),
        ("employers.never_names", "str[]"), ("employers.never_terms?", "str[]"),
        ("cohort.terms", "str[]"), ("cohort.career_start_on_or_after", "ym"),
        ("scoring.prescore_pay_good_annual", "num"),
        ("digest.recipient", "email"), ("digest.weekly_target?", "dict"),
        ("integrations?", "dict"),
        ("telemetry?", "dict"), ("telemetry.budget_usd_per_run?", "num"), ("telemetry.warn_at_fraction?", "num"),
        ("telemetry.pricing?", "dict"), ("telemetry.web_search_usd_per_1k?", "num"),
        ("telemetry.web_fetch_usd_per_1k?", "num"), ("telemetry.batch_multiplier?", "num"),
    ],
    "sources.yaml": [
        ("schema_version", "int"),
        ("search.per_run.connector", "int"), ("search.per_run.web", "int"), ("search.per_run.short_term", "int"),
        ("search.connector_queries", "list"), ("search.web_queries", "list"), ("search.short_term_queries", "list"),
        ("dream_employers", "list"), ("dream_employers.*.name", "str"), ("dream_employers.*.search", "str"),
        ("email_alerts.alert_senders", "str[]"),
    ],
}


def _walk(obj, parts):
    """Yield (path, value) for a dotted path that may contain '*'."""
    if not parts:
        yield "", obj
        return
    head, rest = parts[0], parts[1:]
    if head == "*":
        if isinstance(obj, list):
            for i, item in enumerate(obj):
                for p, v in _walk(item, rest):
                    yield f"[{i}]" + (("." + p) if p and not p.startswith("[") else p), v
        return
    if isinstance(obj, dict) and head in obj:
        for p, v in _walk(obj[head], rest):
            yield head + (("." + p) if p and not p.startswith("[") else p), v
    else:
        yield head + ("." + ".".join(rest) if rest else ""), _MISSING


_MISSING = object()


def _type_ok(v, t):
    if t == "str":
        return isinstance(v, str) and v.strip() != ""
    if t == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "list":
        return isinstance(v, list)
    if t == "dict":
        return isinstance(v, dict)
    if t == "email":
        return isinstance(v, str) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v) is not None
    if t == "ym":
        return isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}", v) is not None
    if t == "str[]":
        return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)
    return False


TYPE_WORDS = {"str": "text", "int": "a whole number", "num": "a number", "list": "a list", "dict": "a section",
              "email": "an email address", "ym": "a year-month like 2027-02", "str[]": "a list of text items"}


def validate(name, data):
    errs = []
    if not isinstance(data, dict):
        return [f"{name}: the file is empty or not a YAML mapping"]
    for path, t in SCHEMA[name]:
        optional = path.endswith("?")
        path = path.rstrip("?")
        for where, v in _walk(data, path.split(".")):
            if v is _MISSING:
                if not optional:
                    errs.append(f"{name}: {where} is missing (expected {TYPE_WORDS[t]})")
            elif v is None and optional:
                continue
            elif not _type_ok(v, t):
                errs.append(f"{name}: {where} should be {TYPE_WORDS[t]}, found {type(v).__name__} {str(v)[:40]!r}")
    return errs


def _cross_checks(cfg):
    errs = []
    goals = cfg["preferences"].get("goals") or []
    if goals and not any(g.get("default") for g in goals):
        errs.append("preferences.yaml: goals - mark one goal `default: true`")
    if len({g.get("id") for g in goals}) != len(goals):
        errs.append("preferences.yaml: goals - every goal needs a different id")
    for g in goals:
        must = g.get("must") or {}
        if g.get("id") == "career" and not isinstance(must.get("pay_floor_annual_usd"), (int, float)):
            errs.append("preferences.yaml: goals[career].must.pay_floor_annual_usd is missing (expected a number)")
        if g.get("id") == "bridge" and not isinstance(must.get("pay_floor_hourly_usd"), (int, float)):
            errs.append("preferences.yaml: goals[bridge].must.pay_floor_hourly_usd is missing (expected a number)")
    wt = (cfg["preferences"].get("digest") or {}).get("weekly_target")
    if isinstance(wt, dict) and not (isinstance(wt.get("min"), int) and isinstance(wt.get("max"), int) and wt["min"] <= wt["max"]):
        errs.append("preferences.yaml: digest.weekly_target needs whole numbers min <= max")
    tel = cfg["preferences"].get("telemetry") or {}
    for m, pr in (tel.get("pricing") or {}).items():
        for f in ("input", "output"):
            if not isinstance((pr or {}).get(f), (int, float)):
                errs.append(f"preferences.yaml: telemetry.pricing.{m}.{f} is missing (expected USD per million tokens)")
    if isinstance(tel.get("warn_at_fraction"), (int, float)) and not 0 < tel["warn_at_fraction"] <= 1:
        errs.append("preferences.yaml: telemetry.warn_at_fraction should be between 0 and 1")
    bases = cfg["bases"]
    if not isinstance(bases, dict) or not bases:
        errs.append("resumes/base_resumes.json: no base resumes found")
        return errs
    known = {}
    for e in cfg["profile"].get("experience") or []:
        known.setdefault((e.get("org"), e.get("title")), set()).update(e.get("bullets") or [])
    for key, b in bases.items():
        if key not in (cfg["profile"].get("resume_bases") or {}):
            errs.append(f"profile.yaml: resume_bases.{key} is missing (base resume '{key}' needs a label)")
        for j in b.get("jobs", []):
            k = (j.get("org"), j.get("title"))
            if k not in known:
                errs.append(f"resumes/base_resumes.json: {key} job '{k[0]} / {k[1]}' is not in profile.yaml experience")
                continue
            for bl in j.get("bullets", []):
                if bl not in known[k]:
                    errs.append(f"resumes/base_resumes.json: {key} bullet not in profile.yaml experience: \"{bl[:60]}\"")
    return errs


_CACHE = {}


def load_config(fresh=False):
    """-> {"profile", "preferences", "sources", "bases", "dir"}; raises ConfigError listing every problem."""
    d = config_dir()
    if d in _CACHE and not fresh:
        return _CACHE[d]
    cfg, errs = {"dir": d}, []
    for name in ("profile.yaml", "preferences.yaml", "sources.yaml"):
        f = os.path.join(d, name)
        if not os.path.exists(f):
            errs.append(f"{name} is missing from {d}")
            cfg[name[:-5]] = {}
            continue
        try:
            data = yaml.safe_load(open(f, encoding="utf-8"))
        except yaml.YAMLError as e:
            errs.append(f"{name}: not valid YAML ({str(e).splitlines()[0]})")
            data = {}
        cfg[name[:-5]] = data or {}
        errs += validate(name, data)
    bf = os.path.join(d, "resumes", "base_resumes.json")
    try:
        cfg["bases"] = json.load(open(bf, encoding="utf-8"))
    except (OSError, ValueError) as e:
        cfg["bases"] = {}
        errs.append(f"resumes/base_resumes.json: can't read it ({e.__class__.__name__})")
    if not errs:
        errs += _cross_checks(cfg)
    if errs:
        raise ConfigError("Config problems in " + d + ":\n  - " + "\n  - ".join(errs))
    _CACHE[d] = cfg
    return cfg


def get(dotted, default=None):
    cur = load_config()
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


# ---------------------------------------------------------------- prompt templates
def template_values():
    c = load_config()
    pr, pf = c["profile"], c["preferences"]
    person = pr["person"]
    integ = pf.get("integrations") or {}
    return {
        "name": person["name"], "first_name": person["first_name"], "recipient": pf["digest"]["recipient"],
        "home": person["home"], "graduation": pr["education"]["graduation"],
        "degree_in_progress": pr["education"].get("degree_in_progress", ""),
        "career_start": pf["cohort"]["career_start_on_or_after"], "cohort_terms": ", ".join(pf["cohort"]["terms"]),
        "config_dir": c["dir"], "state_dir": state_dir(), "base_resumes": cpath("resumes", "base_resumes.json"),
        "tracker_url": integ.get("tracker_url", ""), "drive_resumes_folder": integ.get("drive_resumes_folder", ""),
        "drive_cover_letters_folder": integ.get("drive_cover_letters_folder", ""),
        "alert_senders_or": " OR ".join(c["sources"]["email_alerts"]["alert_senders"]),
        "alert_senders_list": ", ".join(c["sources"]["email_alerts"]["alert_senders"]),
        **_rule_values(pf),
    }


def _rule_values(pf):
    """Prompt sentences built from the role and employer rules; a config without the optional keys gets wording that
    points the helper at the matching preferences.yaml sections instead (never a personal default)."""
    roles, emp = pf.get("roles") or {}, pf.get("employers") or {}
    fam = [x for x in roles.get("families") or [] if x.strip()]
    skip = [x for x in roles.get("skip_families") or [] if x.strip()]
    terms = [x for x in emp.get("never_terms") or [] if x.strip()]
    if fam:
        rf = "Only these role families count: " + ", ".join(fam) + ". Skip everything else"
    else:
        rf = ("Only roles that fit `roles` in preferences.yaml count (its `love`, `ok` and `only_if_great` lists). "
              "Skip roles it rates `no`, titles containing a word from `roles.off_target_title_words`, and everything else")
    rf += f" ({', '.join(skip)})." if skip else "."
    internships = (roles.get("internships") or "").strip()
    return {
        "role_families_rule": rf,
        "never_terms": ", ".join(terms),
        "never_terms_rule": (" Also skip anything that mentions any of these terms (team, product, employer or posting text): "
                             + ", ".join(terms) + ".") if terms else "",
        "internships_rule": internships or "follow `must.internships` of each goal in preferences.yaml.",
    }


def render(text, values=None):
    values = values or template_values()

    def sub(m):
        k = m.group(1)
        if k not in values:
            raise ConfigError(f"prompt placeholder {{{{{k}}}}} has no value")
        return str(values[k])
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", sub, text)


def render_prompts():
    out = os.path.join(ROOT, "out", "prompts")
    os.makedirs(out, exist_ok=True)
    vals = template_values()
    n = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md"))):
        with open(os.path.join(out, os.path.basename(f)), "w", encoding="utf-8") as fh:
            fh.write(render(open(f, encoding="utf-8").read(), vals))
        n += 1
    return out, n


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    try:
        if cmd == "check":
            c = load_config()
            print(f"config ok: {c['dir']} (state: {state_dir()})")
        elif cmd == "summary":
            for k, v in template_values().items():
                print(f"{k}: {v}")
        elif cmd == "state-dir":
            print(state_dir())
        elif cmd == "get":
            v = get(argv[2])
            print(json.dumps(v) if isinstance(v, (list, dict)) else ("" if v is None else v))
        elif cmd == "render-prompts":
            out, n = render_prompts()
            print(f"{n} prompts rendered into {out}")
        else:
            sys.exit(__doc__)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
