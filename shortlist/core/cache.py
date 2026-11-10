"""Company research cache (data/companies.json), refreshed every 60 days.

  python3 scripts/cache.py needs          -> prints companies on out/shortlist.json (and hand-added rows)
                                             that are missing or stale; writes out/research_needed.json
  python3 scripts/cache.py merge          -> merges out/research.jsonl into data/companies.json
  python3 scripts/cache.py for-judge      -> writes out/company_context.json (cache entries for every
                                             shortlisted company, for the Opus step)

research.jsonl lines: {"company": "...", "wlb_glassdoor": 4.1, "overall": 3.8, "size": "~5,000",
  "ownership": "public|private|nonprofit|government|PE-owned|employee-owned", "layoffs_18mo": "none found|...",
  "offices": "...", "pto": "...", "review_phrases": ["...", "..."], "industry": "...",
  "category_flags": ["gen-ai main product" | "defense" | "ad tech" | ...], "sources": ["url", ...], "notes": "..."}
"""
import sys
import datetime as dt
from shortlist.common import outp, load, save, load_jsonl, today, norm_company, dp


def cache():
    return load(dp("companies.json"), {"version": 1, "refresh_days": 60, "companies": {}})


def lookup(c, name):
    n = norm_company(name)
    for k, v in c["companies"].items():
        if norm_company(k) == n:
            return k, v
    return None, None


def stale(entry, days):
    try:
        return (dt.date.fromisoformat(today()) - dt.date.fromisoformat(entry.get("checked", "1970-01-01"))).days > days
    except ValueError:
        return True


def needs():
    c = cache()
    verified = load(outp("verified.json"), {})
    short = load(outp("shortlist.json"), [])
    # only research companies whose posting was verified live (verify runs first)
    names = [x["company"] for x in short if (not verified) or verified.get(x["cid"], {}).get("live")]
    tr = {r["id"]: r for r in load(dp("tracker.json"))["rows"]}
    names += [tr[i]["company"] for i in load(outp("hand_added.json"), []) if i in tr]
    out, seen = [], set()
    for n in names:
        if norm_company(n) in seen:
            continue
        seen.add(norm_company(n))
        _, e = lookup(c, n)
        if not e or stale(e, c.get("refresh_days", 60)):
            out.append(n)
    save(outp("research_needed.json"), out)
    print(f"{len(out)} companies need research: {', '.join(out)}" if out else "all companies cached")


def merge():
    c = cache()
    n = 0
    for r in load_jsonl(outp("research.jsonl")):
        name = r.pop("company", None)
        if not name:
            continue
        k, e = lookup(c, name)
        e = dict(e or {})
        e.update({kk: vv for kk, vv in r.items() if vv not in (None, "", [])})
        e["checked"] = today()
        c["companies"][k or name] = e
        n += 1
    save(dp("companies.json"), c)
    print(f"merged {n} company records")


def for_judge():
    c = cache()
    ctx = {}
    verified = load(outp("verified.json"), {})
    names = [x["company"] for x in load(outp("shortlist.json"), []) if verified.get(x["cid"], {}).get("live")]
    tr = {r["id"]: r for r in load(dp("tracker.json"))["rows"]}
    names += [tr[i]["company"] for i in load(outp("hand_added.json"), []) if i in tr]
    for nme in names:
        k, e = lookup(c, nme)
        ctx[nme] = e or {"unknown": True}
    save(outp("company_context.json"), ctx)
    print(f"context for {len(ctx)} companies")


if __name__ == "__main__":
    {"needs": needs, "merge": merge, "for-judge": for_judge}[sys.argv[1]]()
