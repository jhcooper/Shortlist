"""Keep data/tracker.json and the tracker website's database in step.

  pull <readback_dir> <listing.txt>
      readback_dir = the ArtifactData `out_dir` of a list of "apps" (and "meta");
      listing.txt  = the text ArtifactData printed for those lists (versions are parsed from it).
      The website database is the live copy: its rows replace tracker.json rows.
      Writes out/db_versions.json.

  apply
      Reads out/changes.json (new rows, email events, field updates made by this run),
      merges them onto tracker.json under the edit rules, and writes:
        out/db_writes.json   - the ArtifactData batch `writes` (chunks of <=50 in out/db_writes_<n>.json)
        out/updates.json     - what changed, for the digest
      Rules: a field you edited more recently than an event is left alone (the event is
      noted). Rejections and offers never change Status; they set pending_confirm.
      Every date written is the Eastern date (common.local_date). Applied rows with no stage change for
      STALE_DAYS get needs_follow_up (never a status change).

  check <readback_dir>
      After write-back: confirms every tracker.json row exists in the database with the
      same status. Exit 1 on mismatch.
"""
import sys, os, re, glob, json
import datetime as dt
from common import p, outp, load, save, today, iso_now, norm_url, dedupe_key, local_date, ts_after, TrackedIndex, dp

FIELDS = ["id", "date_found", "track", "company", "role", "location", "work_mode", "posting_url", "source",
          "base_pay", "fit", "chance", "why", "status", "resume_url", "cover_letter_url", "date_applied",
          "contact", "next_step", "follow_up", "notes", "my_notes", "request_cover_letter", "pending_confirm",
          "edits", "last_checked", "updated_by", "updated_at", "resume_spec", "resume_base", "resume_file", "card_why", "card_watch", "card_wlb", "request_resume", "goals",
          "source_detail", "scored_after_apply", "needs_follow_up", "last_follow_up"]
DEFAULTS = {"goals": [], "fit": 0, "scored_after_apply": False, "needs_follow_up": False, "request_cover_letter": False, "request_resume": False, "pending_confirm": {}, "edits": {}, "resume_spec": {}}
OPEN = {"Drafted", "Applied", "OA", "Interview", "Waiting for opening"}


def blank(r):
    out = {k: DEFAULTS.get(k, "") for k in FIELDS}
    out.update({k: v for k, v in r.items() if v is not None})
    return out


def parse_versions(text):
    vers = {}
    # lines like:  - "acme-da-20261020"  1555 bytes  version 1  "/path..."
    for m in re.finditer(r'"([^"]+)"\s+\d+\s+bytes\s+version\s+(\d+)\s+"([^"]+)"', text):
        coll = os.path.basename(os.path.dirname(m.group(3)))
        vers[f"{coll}/{m.group(1)}"] = int(m.group(2))
    return vers


# "Check these yourself" answers are recorded in seen_postings with these verdicts. Older state named them after the
# person ("<name>_open" / "<name>_closed"); those still count as already recorded.
USER_OPEN, USER_CLOSED = "user_open", "user_closed"
# Matches the old person-named verdicts ("<name>_open" / "<name>_closed") so existing seen_postings entries still count.
# It accepts ANY "<word>_open" / "<word>_closed", so new system verdicts must never use that shape (pick names like
# "reopened" or "closed_by_page" instead), or they would be mistaken for by-hand answers.
LEGACY_USER_VERDICT_RE = re.compile(r"[a-z]+_(?:open|closed)")


def is_user_verdict(v):
    """True for a by-hand open/closed answer, under the current or an older verdict name."""
    return isinstance(v, str) and (v in (USER_OPEN, USER_CLOSED) or LEGACY_USER_VERDICT_RE.fullmatch(v) is not None)


def pull(readback, listing):
    vers = parse_versions(open(listing, encoding="utf-8").read())
    tr = load(dp("tracker.json"), {"version": 1, "rows": []})
    by_id = {r["id"]: r for r in tr["rows"]}
    db_rows = {}
    for f in glob.glob(os.path.join(readback, "apps", "*.json")):
        d = load(f)
        rid = d.get("id") or os.path.splitext(os.path.basename(f))[0]
        d["id"] = rid
        db_rows[rid] = blank(d)
    missing = [rid for rid in by_id if rid not in db_rows]
    merged = dict(db_rows)
    for rid in missing:  # in repo but not in the database: keep, flag for re-seed
        merged[rid] = by_id[rid]
    tr["rows"] = sorted(merged.values(), key=lambda r: (r.get("date_found") or "", r["id"]))
    tr["updated"] = today()
    save(dp("tracker.json"), tr)
    save(outp("db_versions.json"), vers)
    save(outp("db_missing.json"), missing)
    # every row without a score gets one from the judge, whatever its source or stage (out/hand_added.json, kept name)
    hand = [r["id"] for r in db_rows.values() if not r.get("fit")]
    # "Check these yourself" answers: closed -> never shown again; open -> row already added by the page
    checks = {}
    for f in glob.glob(os.path.join(readback, "checks", "*.json")):
        d = load(f)
        checks[d.get("key") or os.path.splitext(os.path.basename(f))[0]] = d
    save(outp("checks_state.json"), checks)
    seen = load(dp("seen_postings.json"), {"version": 1, "postings": []})
    done = {(x.get("company"), x.get("title")) for x in seen["postings"] if is_user_verdict(x.get("verdict"))}
    for k, d in checks.items():
        a = d.get("answer")
        if a in ("open", "closed") and (d.get("company"), d.get("title")) not in done:
            seen["postings"].append({"company": d.get("company", ""), "title": d.get("title", ""), "url": d.get("url", ""),
                                     "verdict": USER_OPEN if a == "open" else USER_CLOSED,
                                     "reason": "you checked it by hand", "date": today()})
    save(dp("seen_postings.json"), seen)
    save(outp("hand_added.json"), hand)
    print(f"pulled {len(db_rows)} rows from the website; {len(missing)} only in repo; {len(hand)} rows need a score; {len(vers)} versions")


def edited_after(row, field, when):
    e = (row.get("edits") or {}).get(field)
    return ts_after(e, when)  # real instants: the page stores UTC ("Z"), the run Eastern ("-05:00")


STALE_DAYS = 21


def needs_follow_up(r, today_s=None):
    """Applied for more than STALE_DAYS with no stage change, no future follow-up date (a snooze sets one),
    and no follow-up recorded in the last STALE_DAYS."""
    if r.get("status") != "Applied":
        return False
    t = dt.date.fromisoformat(today_s or today())
    changed = (r.get("edits") or {}).get("status")
    since = (local_date(changed) if changed else "") or r.get("date_applied") or ""  # local_date(None) would mean "now"
    try:
        if not since or (t - dt.date.fromisoformat(since)).days <= STALE_DAYS:
            return False
        fu = r.get("follow_up") or ""
        if fu and fu > t.isoformat():
            return False
        lf = r.get("last_follow_up") or ""
        if lf and (t - dt.date.fromisoformat(lf)).days <= STALE_DAYS:
            return False
    except ValueError:
        return False
    return True


def note(row, text, date=None):
    line = f"{local_date(date) if date else today()}: {text}"
    row["notes"] = (row.get("notes") + "; " if row.get("notes") else "") + line


def apply():
    ch = load(outp("changes.json"), {})
    tr = load(dp("tracker.json"))
    rows = {r["id"]: r for r in tr["rows"]}
    vers = load(outp("db_versions.json"), {})
    missing = set(load(outp("db_missing.json"), []))
    patches, creates, updates = {}, {}, []

    def patch(rid, **kv):
        patches.setdefault(rid, {}).update(kv)
        rows[rid].update(kv)

    # 1. new rows (skip duplicates by id, url, or company+title)
    have_url = {norm_url(r.get("posting_url")) for r in rows.values() if r.get("posting_url")}
    tracked = TrackedIndex(rows.values())  # company + role + location (unknown location = same row)
    for nr in ch.get("new_rows", []):
        nr = blank(nr)
        if nr["id"] in rows or (nr["posting_url"] and norm_url(nr["posting_url"]) in have_url) or tracked.has(nr["company"], nr["role"], nr["location"]):
            continue
        tracked.add(nr["company"], nr["role"], nr["location"])
        nr.update(updated_by="run", updated_at=iso_now())
        rows[nr["id"]] = nr
        creates[nr["id"]] = nr
        updates.append({"kind": "new", "row_id": nr["id"]})

    # 2. email / watch events (each Gmail message is processed once, ever)
    processed = load(dp("processed_messages.json"), {"ids": []})
    done_ids = set(processed["ids"])
    for ev in ch.get("events", []):
        mid = ev.get("message_id")
        if mid and mid in done_ids:
            continue
        if mid:
            done_ids.add(mid)
            processed["ids"].append(mid)
        rid = ev.get("row_id")
        if rid not in rows:
            updates.append({"kind": "unmatched", "text": ev.get("summary", ""), "urgent": ev.get("urgent", False)})
            continue
        r = rows[rid]
        t, when = ev.get("type"), ev.get("date") or iso_now()
        summ = ev.get("summary", "").strip()
        status_locked = edited_after(r, "status", when)
        u = {"row_id": rid, "company": r["company"], "role": r["role"], "kind": t, "text": summ,
             "urgent": t in ("assessment", "interview", "reopened", "offer") or bool(ev.get("deadline"))}
        if ev.get("deadline"):
            u["deadline"] = ev["deadline"]
        if t == "confirmation" and r["status"] not in ("Drafted", "", "Waiting for opening") and r.get("date_applied"):
            continue  # already recorded; nothing new
        if t == "confirmation":
            if r["status"] in ("Drafted", "", "Waiting for opening") and not status_locked:
                patch(rid, status="Applied")
            if not r.get("date_applied") and not edited_after(r, "date_applied", when):
                patch(rid, date_applied=local_date(when))
        elif t == "assessment":
            if r["status"] in ("Drafted", "Applied", "") and not status_locked:
                patch(rid, status="OA")
            if not edited_after(r, "next_step", when):
                patch(rid, next_step=ev.get("next_step") or summ)
        elif t == "interview":
            if r["status"] not in ("Interview", "Offer", "Accepted") and not status_locked:
                patch(rid, status="Interview")
            if not edited_after(r, "next_step", when):
                patch(rid, next_step=ev.get("next_step") or summ)
        elif t in ("rejection", "offer"):
            kind = "Rejected" if t == "rejection" else "Offer"
            if r["status"] != kind and r["status"] != "Accepted":  # an accepted offer is final; only you change it
                patch(rid, pending_confirm={"type": kind, "evidence": summ[:200], "detected": local_date(when), "answered": "", "answer": ""})
                if not edited_after(r, "next_step", when):
                    patch(rid, next_step=f"Confirm: {kind.lower()}? (from email {local_date(when)})")
                u["text"] = f"Possible {'rejection' if kind == 'Rejected' else 'offer'}: {summ}. Confirm it on the tracker."
        elif t == "reply_needed":
            if not edited_after(r, "next_step", when):
                patch(rid, next_step=f"Reply needed: {ev.get('next_step') or summ}")
        elif t == "reopened":
            patch(rid, last_checked=today())
            if not edited_after(r, "next_step", when):
                patch(rid, next_step=f"Reopened {today()} - apply")
        elif t == "watch_checked":
            patch(rid, last_checked=today())
            u = None
        elif t == "follow_up_nudge":
            if not r.get("follow_up") and not edited_after(r, "follow_up", when):
                patch(rid, follow_up=ev.get("follow_up") or today())
        if t not in ("watch_checked",):
            locked = " (stage left as you set it)" if status_locked and t in ("confirmation", "assessment", "interview") else ""
            note(r, (summ or t) + locked, when)
            patches.setdefault(rid, {})["notes"] = r["notes"]
        if u:
            updates.append(u)

    processed["ids"] = processed["ids"][-5000:]
    save(dp("processed_messages.json"), processed)

    # 3. field updates from the run (cover-letter links, research for hand-added rows, ...)
    for fu in ch.get("field_updates", []):
        rid = fu.get("row_id")
        if rid not in rows:
            continue
        at = fu.get("at") or iso_now()
        for k, v in (fu.get("fields") or {}).items():
            if k in ("edits", "id") or edited_after(rows[rid], k, at):
                continue
            patch(rid, **{k: v})
        if fu.get("note"):
            note(rows[rid], fu["note"])
            patches.setdefault(rid, {})["notes"] = rows[rid]["notes"]
        if fu.get("update_text"):
            updates.append({"kind": fu.get("kind", "info"), "row_id": rid, "company": rows[rid]["company"], "role": rows[rid]["role"], "text": fu["update_text"], "urgent": False})

    # 3b. stale applications: flag for a follow-up (shown in Today); never change status
    for rid, r in rows.items():
        want = needs_follow_up(r)
        if want and not r.get("needs_follow_up"):
            patch(rid, needs_follow_up=True)
            updates.append({"kind": "follow_up_nudge", "row_id": rid, "company": r["company"], "role": r["role"],
                            "text": f"No reply in over {STALE_DAYS} days; consider a follow-up.", "urgent": False})
        elif r.get("needs_follow_up") and r.get("status") != "Applied":
            patch(rid, needs_follow_up=False)

    # 4. build ArtifactData writes
    stamp = iso_now()
    pdir = outp("db_patch")
    os.makedirs(pdir, exist_ok=True)
    for f in glob.glob(os.path.join(pdir, "*.json")):
        os.remove(f)
    writes = []
    for rid, doc in creates.items():
        save(os.path.join(pdir, f"{rid}.json"), doc)
        writes.append({"op": "set", "collection": "apps", "doc_id": rid, "file_path": os.path.join(pdir, f"{rid}.json")})
    for rid in missing:
        if rid in rows and rid not in creates:
            save(os.path.join(pdir, f"{rid}.json"), blank(rows[rid]))
            writes.append({"op": "set", "collection": "apps", "doc_id": rid, "file_path": os.path.join(pdir, f"{rid}.json")})
    for rid, pt in patches.items():
        if rid in creates or rid in missing:
            if rid in creates:
                save(os.path.join(pdir, f"{rid}.json"), rows[rid])
            continue
        pt = dict(pt, updated_by="run", updated_at=stamp)
        rows[rid].update(updated_by="run", updated_at=stamp)
        save(os.path.join(pdir, f"{rid}.json"), pt)
        w = {"op": "update", "collection": "apps", "doc_id": rid, "file_path": os.path.join(pdir, f"{rid}.json")}
        if f"apps/{rid}" in vers:
            w["if_version"] = vers[f"apps/{rid}"]
        writes.append(w)
    # check-these-yourself items: new ones from today's judge, answered or stale ones marked processed (never deleted)
    cstate = load(outp("checks_state.json"), {})
    import datetime as _dt
    have_ck = {dedupe_key(d.get("company", ""), d.get("title", "")) for d in cstate.values()}
    have_ck |= {dedupe_key(r["company"], r["role"]) for r in rows.values()}
    for item in ch.get("checks", []):
        k = item["key"]
        dk = dedupe_key(item["company"], item["title"])
        if k in cstate or dk in have_ck:
            continue
        have_ck.add(dk)
        save(os.path.join(pdir, f"check-{k}.json"), item)
        writes.append({"op": "set", "collection": "checks", "doc_id": k, "file_path": os.path.join(pdir, f"check-{k}.json")})
    for k, d in cstate.items():
        if d.get("processed"):
            continue
        stale = False
        try:
            stale = (_dt.date.fromisoformat(today()) - _dt.date.fromisoformat(str(d.get("date", today()))[:10])).days > 10
        except ValueError:
            pass
        if d.get("answer") in ("open", "closed") or stale:
            body = {"processed": True, "processed_at": stamp, "expired": bool(stale and not d.get("answer"))}
            save(os.path.join(pdir, f"check-{k}.json"), body)
            w = {"op": "update", "collection": "checks", "doc_id": k, "file_path": os.path.join(pdir, f"check-{k}.json")}
            if f"checks/{k}" in vers:
                w["if_version"] = vers[f"checks/{k}"]
            writes.append(w)
    meta = ch.get("meta")
    if meta:
        for doc_id, body in meta.items():
            save(os.path.join(pdir, f"meta-{doc_id}.json"), body)
            w = {"op": "set", "collection": "meta", "doc_id": doc_id, "file_path": os.path.join(pdir, f"meta-{doc_id}.json")}
            if f"meta/{doc_id}" in vers:
                w["if_version"] = vers[f"meta/{doc_id}"]
            writes.append(w)
    for f in glob.glob(outp("db_writes_*.json")):
        os.remove(f)
    chunks = [writes[i:i + 50] for i in range(0, len(writes), 50)] or [[]]
    for i, c in enumerate(chunks, 1):
        save(outp(f"db_writes_{i}.json"), c)
    save(outp("db_writes.json"), writes)
    tr["rows"] = sorted(rows.values(), key=lambda r: (r.get("date_found") or "", r["id"]))
    tr["updated"] = today()
    save(dp("tracker.json"), tr)
    save(outp("updates.json"), updates)
    print(f"{len(creates)} new rows, {len(patches)} rows changed, {len(writes)} writes in {len(chunks)} batch file(s): " +
          ", ".join(os.path.basename(outp(f'db_writes_{i}.json')) for i in range(1, len(chunks) + 1)))


def check(readback):
    tr = load(dp("tracker.json"))
    db = {}
    for f in glob.glob(os.path.join(readback, "apps", "*.json")):
        d = load(f)
        db[d.get("id") or os.path.splitext(os.path.basename(f))[0]] = d
    bad = [r["id"] for r in tr["rows"] if r["id"] not in db or db[r["id"]].get("status") != r.get("status")]
    if bad:
        print("MISMATCH:", ", ".join(bad))
        from errors import log
        log("write_back", "db_write_conflict", ", ".join(bad), "check_mismatch", "tracker rows differ from database after write-back", helper="scripts")
        sys.exit(1)
    print(f"ok: {len(tr['rows'])} rows match")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "pull":
        pull(sys.argv[2], sys.argv[3])
    elif cmd == "apply":
        apply()
    elif cmd == "check":
        check(sys.argv[2])
    else:
        print(__doc__)
