"""Structured error log for each run.

Events are JSON lines:
  {"time", "step", "helper", "kind", "target", "domain", "error_type", "message", "retried", "resolved"}

kind is one of KINDS below. Helpers write their own file out/errors/<helper>.jsonl; scripts and the coordinating
session append with `log`. `collect` merges everything into data/errors/<date>.jsonl (committed with the run).

  python3 scripts/errors.py log <step> <kind> <target> <error_type> "<message>" [--retried] [--resolved] [--helper X]
  python3 scripts/errors.py collect      -> data/errors/<today>.jsonl
  python3 scripts/errors.py summary      -> out/error_summary.json (counts, footer lines, new/spiking vs last 5 runs)
"""
import sys
import os
import glob
import json
import re
from urllib.parse import urlparse
from common import outp, save, load_jsonl, today, iso_now, dp

KINDS = {"fetch_blocked", "fetch_failed", "search_empty", "connector_error", "upload_mismatch",
         "validation_failed", "db_write_conflict", "parse_error", "timeout", "other"}
LABEL = {"fetch_blocked": "fetches refused", "fetch_failed": "fetches failed", "search_empty": "searches found nothing",
         "connector_error": "job-site connector errors", "upload_mismatch": "Drive uploads incomplete",
         "validation_failed": "resume checks failed", "db_write_conflict": "tracker write conflicts",
         "parse_error": "unreadable helper outputs", "timeout": "timeouts", "other": "other problems"}


def domain(target):
    t = (target or "").strip()
    if t.startswith("http"):
        return urlparse(t).netloc.lower().removeprefix("www.")
    m = re.search(r"site:([\w.\-]+)", t)
    return m.group(1).lower() if m else ""


def event(step, kind, target="", error_type="", message="", retried=False, resolved=False, helper="coordinator"):
    kind = kind if kind in KINDS else "other"
    return {"time": iso_now(), "step": step, "helper": helper, "kind": kind, "target": target[:300],
            "domain": domain(target), "error_type": error_type[:80], "message": message[:300],
            "retried": bool(retried), "resolved": bool(resolved)}


def log(step, kind, target="", error_type="", message="", retried=False, resolved=False, helper="coordinator"):
    os.makedirs(outp("errors"), exist_ok=True)
    with open(outp("errors", f"{helper}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event(step, kind, target, error_type, message, retried, resolved, helper), ensure_ascii=False) + "\n")


def collect():
    rows, seen = [], set()
    for f in sorted(glob.glob(outp("errors", "*.jsonl"))):
        helper = os.path.splitext(os.path.basename(f))[0]
        for e in load_jsonl(f):
            e.setdefault("helper", helper)
            e["kind"] = e.get("kind") if e.get("kind") in KINDS else "other"
            e.setdefault("domain", domain(e.get("target", "")))
            key = (e.get("helper"), e.get("kind"), e.get("target"), e.get("error_type"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(e)
    path = dp("errors", f"{today()}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in rows:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{len(rows)} error events -> data/errors/{today()}.jsonl")


def counts(rows):
    c = {}
    for e in rows:
        if e.get("resolved"):
            continue
        k = (e.get("kind"), e.get("error_type") or "")
        c[k] = c.get(k, 0) + 1
    return c


def summary():
    files = sorted(glob.glob(dp("errors", "*.jsonl")))
    cur_path = dp("errors", f"{today()}.jsonl")
    cur = load_jsonl(cur_path)
    past = [f for f in files if f != cur_path][-5:]
    now_c = counts(cur)
    hist = [counts(load_jsonl(f)) for f in past]
    lines, flags = [], []
    for (kind, et), n in sorted(now_c.items(), key=lambda x: -x[1]):
        doms = sorted({e["domain"] for e in cur if e.get("kind") == kind and (e.get("error_type") or "") == et and e.get("domain")})
        dom_txt = f" ({', '.join(doms[:5])}{'…' if len(doms) > 5 else ''})" if doms else ""
        lines.append(f"{n} {LABEL.get(kind, kind)}{' [' + et + ']' if et else ''}{dom_txt}")
        prev = [h.get((kind, et), 0) for h in hist]
        avg = sum(prev) / len(prev) if prev else 0
        if past and (avg == 0 and n >= 2 or avg > 0 and n >= 3 * avg and n - avg >= 3):
            flags.append(f"NEW or spiking: {n} {LABEL.get(kind, kind)}{' [' + et + ']' if et else ''} (avg {avg:.1f} over last {len(prev)} runs)")
    total = sum(now_c.values())
    out = {"date": today(), "total_unresolved": total, "lines": lines, "flags": flags,
           "by_kind": {k: sum(n for (kk, _), n in now_c.items() if kk == k) for k in {k for k, _ in now_c}}}
    save(outp("error_summary.json"), out)
    print(f"{total} unresolved; {len(flags)} new/spiking" + ("".join("\n  " + f for f in flags)))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "log":
        a = [x for x in sys.argv[2:] if not x.startswith("--")]
        helper = sys.argv[sys.argv.index("--helper") + 1] if "--helper" in sys.argv else "coordinator"
        if helper in a:
            a.remove(helper)
        a += [""] * (5 - len(a))
        log(a[0], a[1], a[2], a[3], a[4], "--retried" in sys.argv, "--resolved" in sys.argv, helper)
    elif cmd == "collect":
        collect()
    elif cmd == "summary":
        summary()
    else:
        print(__doc__)
