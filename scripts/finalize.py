"""Assemble the run's results.

  python3 scripts/finalize.py changes   -> out/changes.json (for sync_tracker.py apply) + seen_postings update
  python3 scripts/finalize.py runlog <status> [email_mode]
                                        -> data/runs/<date>.json  (status: done | done_no_email | failed)
  python3 scripts/finalize.py sent      -> marks email_sent: true in today's run log

Inputs (all optional except what the step produced):
  out/shortlist.json      prefilter.py          out/verified.json    Haiku verify step  {cid: {...}}
  out/judged.json         Opus step              out/resumes.json     {cid: drive_view_url}
  out/inbox_events.json   Haiku inbox step       out/cover_letters.json {row_id: drive_view_url}
  out/readback/meta/near_misses.json   (from the start-of-run database read)
  out/problems.json       list of strings for the email footer
"""
import sys, os
from common import p, outp, load, save, today, iso_now, row_id, load_jsonl, dp
from sources import normalize_source

from config import load_config

_CFG = load_config()
GOALS = _CFG["preferences"]["goals"]
GOAL_NAME = {g["id"]: g["name"] for g in GOALS}
LEGACY = {"short": "bridge", "long": "career"}


def pay_floor(g):
    """Annual pay below which the page marks a role's pay in amber: the goal's preferred minimum, else its floor."""
    pref = ((g.get("prefer") or {}).get("pay") or {}).get("prefer_base_min_usd")
    v = pref or (g.get("must") or {}).get("pay_floor_annual_usd")
    return {"pay_floor": v} if v else {}


def page_settings():
    """What the tracker page shows about you, from config (the page keeps no personal defaults)."""
    pr, pf = _CFG["profile"], _CFG["preferences"]
    person = pr["person"]
    prof = {k: person[k] for k in ("id", "name", "first_name", "tagline", "title", "phone", "email", "links") if person.get(k)}
    prof["location"] = person.get("home", "")
    prof["education"] = pr["education"]["resume_lines"]
    out = {"profile": prof, "run_time": (pf.get("digest") or {}).get("start_time_et", ""), "resume_bases": {k: {"label": v.get("label", k)} for k, v in (pr.get("resume_bases") or {}).items()}}
    wt = (pf.get("digest") or {}).get("weekly_target", "unset")
    if wt != "unset":
        out["weekly_target"] = wt  # null hides the weekly meter
    return out


def source_fields(raw):
    """Category + original text; an unmappable source is kept as is and logged rather than guessed."""
    cat, detail = normalize_source(raw)
    if not cat:
        try:
            from errors import log
            log("finalize", "other", raw, "unmapped_source", "source text did not match the shared mapping", helper="scripts")
        except Exception:
            pass
        return {"source": raw, "source_detail": raw}
    return {"source": cat, "source_detail": detail}


def goal_label(pk, c):
    """Tracker label for a pick: the judge's goal, else the candidate's first goal, else the default goal."""
    g = pk.get("goal") or LEGACY.get(pk.get("track")) or ((c.get("goals") or [None])[0]) or LEGACY.get(c.get("track"))
    return GOAL_NAME.get(g) or next((x["name"] for x in GOALS if x.get("default")), GOALS[0]["name"])


def changes():
    short = {c["cid"]: c for c in load(outp("shortlist.json"), [])}
    verified = load(outp("verified.json"), {})
    judged = load(outp("judged.json"), {})
    resumes = load(outp("resumes.json"), {})
    letters = load(outp("cover_letters.json"), {})
    new_rows, picks_ids = [], []
    for pk in judged.get("picks", []):
        c = short.get(pk.get("cid"), {})
        card = pk.get("card", {})
        row = pk.get("row", {})
        company = card.get("company") or c.get("company", "")
        role = card.get("role") or c.get("title", "")
        rid = row_id(company, role)
        pk["row_id"] = rid
        built = load(outp("built.json"), {})
        spec = load(outp("docs", f"{pk.get('cid')}.json"), {}) if pk.get("cid") in built else {}
        if spec:
            spec = {k: spec[k] for k in ("tagline", "summary", "jobs", "skills") if k in spec}
        new_rows.append({
            "id": rid, "date_found": today(), "track": goal_label(pk, c), "goals": [GOAL_NAME.get(g, g) for g in (c.get("goals") or [])],
            "company": company, "role": role, "location": row.get("location") or c.get("location", ""),
            "work_mode": row.get("work_mode") or c.get("work_mode", ""), "posting_url": card.get("url") or c.get("url", ""),
            **source_fields(c.get("source", "") or "WebSearch"), "base_pay": row.get("base_pay") or card.get("pay", ""),
            "fit": pk.get("fit", 0), "chance": pk.get("chance", ""), "why": row.get("why", ""),
            "status": "Drafted", "resume_url": resumes.get(pk.get("cid"), ""), "cover_letter_url": "",
            "date_applied": "", "contact": "", "next_step": "Review resume; apply" if resumes.get(pk.get("cid")) else "Review posting; apply",
            "follow_up": card.get("deadline", "") or "", "notes": f"{today()}: found by run (Fit {pk.get('fit')})", "my_notes": "",
            "request_cover_letter": False, "pending_confirm": {}, "edits": {}, "last_checked": "",
            "card_why": card.get("why_fits", ""), "card_watch": card.get("watch_outs", ""), "card_wlb": card.get("wlb", ""),
            "resume_spec": spec, "resume_base": (pk.get("tailor") or {}).get("base", "") if spec else "",
            "resume_file": built.get(pk.get("cid"), "") if spec else "",
        })
        picks_ids.append(rid)
    save(outp("judged.json"), judged)  # now carries row_id per pick for the email

    field_updates = []
    built_all = load(outp("built.json"), {})
    tracker_rows = {r["id"]: r for r in load(dp("tracker.json"))["rows"]}
    for u in judged.get("row_updates", []):
        rid = u.get("row_id")
        row = tracker_rows.get(rid, {})
        if row.get("status") not in ("Drafted", "", None):  # scored after you applied: score only, no resume, stage untouched
            f = {k: v for k, v in (u.get("fields") or {}).items() if k not in ("status", "resume_spec", "resume_base", "resume_file")}
            field_updates.append({"row_id": rid, "fields": dict(f, scored_after_apply=True), "note": "run scored it (already applied)"})
            continue
        if rid in built_all:  # a confirmed/hand-added row the judge scored Fit 4+ and that got a tailored resume
            sp = load(outp("docs", f"{rid}.json"), {})
            u.setdefault("fields", {}).update({"resume_spec": {k: sp[k] for k in ("tagline", "summary", "jobs", "skills") if k in sp},
                                              "resume_base": (u.get("tailor") or {}).get("base", ""), "resume_file": built_all[rid]})
        field_updates.append({"row_id": u["row_id"], "fields": u.get("fields", {}), "note": "run added research"})
    # resumes you asked for with "Draft a resume"
    for r in load(dp("tracker.json"))["rows"]:
        rid = r["id"]
        if r.get("request_resume") and rid in built_all and not any(fu.get("row_id") == rid and "resume_spec" in fu.get("fields", {}) for fu in field_updates):
            sp = load(outp("docs", f"{rid}.json"), {})
            field_updates.append({"row_id": rid, "fields": {"resume_spec": {k: sp[k] for k in ("tagline", "summary", "jobs", "skills") if k in sp},
                                  "resume_base": sp.get("_base") or next(iter(_CFG["bases"])), "resume_file": built_all[rid], "request_resume": False, "next_step": "Review resume; apply"},
                                  "note": "tailored resume drafted on request", "kind": "info", "update_text": "Tailored resume drafted as you asked; download it from the tracker."})
    for rid, url in letters.items():
        field_updates.append({"row_id": rid, "fields": {"cover_letter_url": url, "request_cover_letter": False},
                              "note": "cover letter drafted", "kind": "cover_letter", "update_text": "Cover letter drafted (Word file in Drive)."})

    nm_today = []
    for n in judged.get("near_misses", []):
        c = short.get(n.get("cid"), {})
        nm_today.append({"company": n.get("company") or c.get("company", ""), "title": n.get("title") or c.get("title", ""),
                         "url": n.get("url") or c.get("url", ""), "verdict": "near_miss", "reason": n.get("reason", ""), "date": today()})
    old = load(outp("readback", "meta", "near_misses.json"), {}).get("items", [])
    nm_all = (nm_today + old)[:15]
    raw_n = 0
    if os.path.isdir(outp("raw")):
        raw_n = sum(len(load_jsonl(os.path.join(outp("raw"), f))) for f in os.listdir(outp("raw")))
    es = load(outp("error_summary.json"), {})
    upd_n = len([e for e in load(outp("inbox_events.json"), []) if e.get("type") != "watch_checked"])
    counts = {"found": raw_n, "shortlisted": len(short), "verified": sum(1 for v in verified.values() if v.get("live") is True),
              "picks": len(picks_ids), "updates": upd_n}
    meta = {"state": {"last_run": today(), "today_picks": picks_ids, "run_summary": judged.get("summary", ""), "tracker_version": 1,
                      "goals": [dict({"id": g["id"], "name": g["name"], "default": bool(g.get("default"))}, **pay_floor(g)) for g in GOALS],
                      **page_settings(),
                      "counts": counts, "error_total": es.get("total_unresolved", 0), "error_flags": es.get("flags", [])},
            "near_misses": {"items": nm_all},
            "bases": {"resumes": _CFG["bases"]}}

    from common import slug
    checks = []
    for c0 in judged.get("check_yourself", [])[:6]:
        c = short.get(c0.get("cid"), {})
        company, title = c0.get("company") or c.get("company", ""), c0.get("title") or c.get("title", "")
        if not company or not title:
            continue
        checks.append({"key": slug(company, title, maxlen=80), "company": company, "title": title,
                       "url": c0.get("url") or (verified.get(c0.get("cid"), {}) or {}).get("employer_url") or c.get("url", ""),
                       "location": c.get("location", ""), "track": goal_label(c0, c),
                       "dream": bool(c.get("dream")), "why": c0.get("why", ""), "reason": c0.get("reason", ""),
                       "pay": c0.get("pay") or c.get("pay_text", ""), "date": today(), "answer": "", "processed": False})
    save(outp("changes.json"), {"new_rows": new_rows, "checks": checks, "events": load(outp("inbox_events.json"), []),
                                "field_updates": field_updates, "meta": meta})

    # remember every shortlisted posting so it is never evaluated twice
    seen = load(dp("seen_postings.json"), {"version": 1, "postings": []})
    # retry: true = set aside only because something was uncertain (blocked page, aggregator only, not verified);
    # prefilter.py brings those back after a few days. Check-yourself items wait for your answer instead.
    decided = {pk.get("cid"): ("picked", f"Fit {pk.get('fit')}", False) for pk in judged.get("picks", [])}
    decided.update({n.get("cid"): ("near_miss", n.get("reason", ""), bool(n.get("retry"))) for n in judged.get("near_misses", []) if n.get("cid")})
    decided.update({s.get("cid"): ("skip", s.get("reason", ""), bool(s.get("retry"))) for s in judged.get("skipped", []) if s.get("cid")})
    decided.update({k.get("cid"): ("check_yourself", k.get("reason", ""), False) for k in judged.get("check_yourself", []) if k.get("cid")})
    for cid, v in verified.items():
        if cid not in decided and not v.get("live", True):
            decided[cid] = ("closed", v.get("reason", "not open"), False)
    for cid, (verdict, reason, retry) in decided.items():
        c = short.get(cid)
        if c:
            seen["postings"].append({"company": c["company"], "title": c["title"], "url": c["url"], "verdict": verdict,
                                     "reason": reason, "retry": retry, "date": today()})
    seen["postings"] = seen["postings"][-3000:]
    save(dp("seen_postings.json"), seen)
    print(f"{len(new_rows)} new rows, {len(field_updates)} field updates, {len(meta['near_misses']['items'])} near misses kept, {len(decided)} postings marked seen")


def _tel_brief():
    """Short telemetry block for the run log (API version only; the full record is in STATE_DIR/telemetry)."""
    t = load(outp("telemetry_summary.json"), {})
    keep = ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "web_search_requests", "cache_hit_rate",
            "cost_usd", "cost_complete", "budget_usd", "cost_per_pick_usd", "flags")
    return {k: t.get(k) for k in keep}


def runlog(status, mode=""):
    path = dp("runs", f"{today()}.json")
    log = load(path, {"date": today()})
    judged = load(outp("judged.json"), {})
    log.update({
        "status": status, "finished_at": iso_now(), "email_mode": mode,
        "counts": {
            "raw": sum(len(load_jsonl(os.path.join(outp("raw"), f))) for f in os.listdir(outp("raw"))) if os.path.isdir(outp("raw")) else 0,
            "shortlist": len(load(outp("shortlist.json"), [])), "dropped": len(load(outp("dropped.json"), [])),
            "verified": len(load(outp("verified.json"), {})), "picks": len(judged.get("picks", [])),
            "near_misses": len(judged.get("near_misses", [])), "updates": len(load(outp("updates.json"), [])),
        },
        "picks": [{"row_id": pk.get("row_id"), "fit": pk.get("fit")} for pk in judged.get("picks", [])],
        "problems": load(outp("problems.json"), []),
        "errors": {k: v for k, v in load(outp("error_summary.json"), {}).items() if k in ("total_unresolved", "by_kind", "flags")},
        "timings": load(outp("timings.json"), {}),
        **({"telemetry": _tel_brief()} if os.path.exists(outp("telemetry_summary.json")) else {}),
    })
    log.setdefault("email_sent", False)
    save(path, log)
    print(f"run log written: {status}")


def sent():
    path = dp("runs", f"{today()}.json")
    log = load(path, {"date": today()})
    log["email_sent"] = True
    log["email_sent_at"] = iso_now()
    save(path, log)
    print("marked email_sent")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "changes":
        changes()
    elif cmd == "runlog":
        runlog(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif cmd == "sent":
        sent()
