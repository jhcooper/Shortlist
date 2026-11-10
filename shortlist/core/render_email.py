"""Build the digest.

  python3 scripts/render_email.py
      reads  out/judged.json, out/updates.json, out/resumes.json, out/problems.json, data/tracker.json
      writes out/email.json  {"mode": "full"|"short"|"none", "subject", "html", "text", "urgent": bool}
Mode: any new role -> full; only updates -> short; nothing -> none (no email).
"""
import html as H
import re
from shortlist.common import outp, load, save, now_et, dp, tracker_url
from shortlist.config import get as cfg_get

TRACKER_URL = tracker_url()

KIND_ORDER = {"reopened": 0, "interview": 1, "assessment": 2, "offer": 3, "rejection": 4, "reply_needed": 5,
              "confirmation": 6, "follow_up_nudge": 7, "cover_letter": 8, "unmatched": 9, "info": 10}
KIND_LABEL = {"reopened": "Reopened, apply now", "interview": "Interview", "assessment": "Assessment", "offer": "Offer?",
              "rejection": "Rejection?", "reply_needed": "Reply needed", "confirmation": "Application confirmed",
              "follow_up_nudge": "Consider a follow-up", "cover_letter": "Cover letter ready", "unmatched": "Recruiter mail", "info": "Update"}
INK, MUTED, LINE, ACCENT, WARN = "#16211f", "#5b6a66", "#d6dedb", "#17695f", "#a2650f"


def e(s):
    return H.escape(str(s or ""))


def a(url, label):
    return f'<a href="{e(url)}" style="color:{ACCENT}">{e(label)}</a>' if url else e(label)


def card_html(pk, resume):
    c = pk.get("card", {})
    rows = [("Work-life balance", c.get("wlb")), ("Location", c.get("location")), ("Pay", c.get("pay")),
            ("Chance", c.get("chance_line") or pk.get("chance")), ("What they do", c.get("what")),
            ("Why it fits you", c.get("why_fits")), ("Growth", c.get("growth")), ("Watch-outs", c.get("watch_outs")),
            ("Verified open", c.get("verified"))]
    body = "".join(f'<tr><td style="padding:3px 12px 3px 0;color:{MUTED};vertical-align:top;white-space:nowrap;font-size:13px">{e(k)}</td>'
                   f'<td style="padding:3px 0;font-size:14px">{e(v)}</td></tr>' for k, v in rows if v)
    links = [a(c.get("url"), "Posting")] + ([a(resume, "Resume in Drive")] if resume else []) + ([a(TRACKER_URL, "Preview or download the resume on your tracker")] if pk.get("tailor") else [])
    dl = f' &middot; <b style="color:{WARN}">Deadline {e(c.get("deadline"))}</b>' if c.get("deadline") else ""
    return (f'<div style="border:1px solid {LINE};border-radius:8px;padding:14px 16px;margin:0 0 14px">'
            f'<div style="font-size:16px;font-weight:600">{a(c.get("url"), (c.get("company") or "") + " — " + (c.get("role") or ""))}</div>'
            f'<div style="color:{MUTED};font-size:13px;margin:2px 0 8px">Fit {e(pk.get("fit"))}/5{dl}</div>'
            f'<table style="border-collapse:collapse">{body}</table>'
            f'<div style="margin-top:8px;font-size:14px">{" &middot; ".join(links)}</div></div>')


def card_text(pk, resume):
    c = pk.get("card", {})
    lines = [f"{c.get('company')} — {c.get('role')}  (Fit {pk.get('fit')}/5)", c.get("url", "")]
    for k, v in (("WLB", c.get("wlb")), ("Location", c.get("location")), ("Pay", c.get("pay")), ("Chance", c.get("chance_line")),
                 ("What", c.get("what")), ("Why", c.get("why_fits")), ("Growth", c.get("growth")), ("Watch-outs", c.get("watch_outs")),
                 ("Verified", c.get("verified")), ("Deadline", c.get("deadline"))):
        if v:
            lines.append(f"- {k}: {v}")
    if resume:
        lines.append(f"Resume: {resume}")
    return "\n".join(lines)


def main():
    judged = load(outp("judged.json"), {})
    updates = [u for u in load(outp("updates.json"), []) if u.get("kind") not in ("new", "watch_checked")]
    resumes = load(outp("resumes.json"), {})
    problems = load(outp("problems.json"), [])
    picks = judged.get("picks", [])
    goals = cfg_get("preferences.goals")
    default = next((g["id"] for g in goals if g.get("default")), goals[0]["id"])

    def gof(x):
        return x.get("goal") or {"short": "bridge", "long": "career"}.get(x.get("track")) or default

    groups = [(g.get("email_heading") or g["name"] + " roles", sorted([x for x in picks if gof(x) == g["id"]], key=lambda x: -x.get("fit", 0))) for g in goals]
    known = {g["id"] for g in goals}
    other = [x for x in picks if gof(x) not in known]
    if other:
        groups[0] = (groups[0][0], groups[0][1] + other)
    nms = judged.get("near_misses", [])[:5]
    updates.sort(key=lambda u: (not u.get("urgent"), KIND_ORDER.get(u.get("kind"), 99)))
    mode = "full" if (picks or judged.get("check_yourself")) else ("short" if updates else "none")
    urgent = any(u.get("urgent") for u in updates)
    d = now_et()
    day = d.strftime("%a, %b ") + str(d.day)
    nnew = len(picks)
    subj = f"Job digest – {day}: " + (f"{nnew} new role{'s' if nnew != 1 else ''}" if nnew else "updates only")
    if updates and nnew:
        subj += f" · {len(updates)} update{'s' if len(updates) != 1 else ''}"
    elif updates:
        subj = f"Job digest – {day}: {len(updates)} update{'s' if len(updates) != 1 else ''}"
    if urgent:
        subj = "[ACTION] " + subj

    h, t = [], []
    lead = judged.get("summary") or ""
    if lead:
        h.append(f'<p style="font-size:15px;margin:0 0 18px">{e(lead)}</p>')
        t.append(lead + "\n")
    if updates:
        h.append('<h2 style="font-size:17px;margin:18px 0 8px">Updates</h2><ul style="padding-left:18px;margin:0 0 8px">')
        t.append("UPDATES")
        for u in updates:
            who = f"{u.get('company', '')} — {u.get('role', '')}".strip(" —")
            lab = KIND_LABEL.get(u.get("kind"), "Update")
            dl = f" (deadline {u['deadline']})" if u.get("deadline") else ""
            style = f"color:{WARN};font-weight:600" if u.get("urgent") else "font-weight:600"
            h.append(f'<li style="margin:0 0 6px"><span style="{style}">{e(lab)}{e(dl)}</span> · {e(who)}: {e(u.get("text"))}</li>')
            t.append(f"- {lab}{dl} · {who}: {u.get('text', '')}")
        h.append("</ul>")
        t.append("")
    for title, group in groups:
        if group:
            h.append(f'<h2 style="font-size:17px;margin:22px 0 10px">{title}</h2>')
            t.append(title.upper())
            for pk in group:
                r = resumes.get(pk.get("cid"), "")
                h.append(card_html(pk, r))
                t.append(card_text(pk, r) + "\n")
    chk = judged.get("check_yourself", [])[:6]
    if chk:
        h.append(f'<h2 style="font-size:17px;margin:22px 0 4px">Check these yourself</h2><p style="margin:0 0 8px;color:{MUTED};font-size:13px">Strong matches the run could not confirm are open. Open the link, then answer "Was this job open?" on {a(TRACKER_URL, "your tracker")}.</p><ul style="padding-left:18px;margin:0">')
        t.append("CHECK THESE YOURSELF (answer on the tracker: " + TRACKER_URL + ")")
        for c in chk:
            h.append(f'<li style="margin:0 0 6px">{a(c.get("url"), (c.get("company") or "") + " — " + (c.get("title") or ""))}: {e(c.get("why"))} <span style="color:{MUTED}">({e(c.get("reason"))})</span></li>')
            t.append(f"- {c.get('company')} — {c.get('title')}: {c.get('why', '')} ({c.get('reason', '')}) {c.get('url', '')}")
        h.append("</ul>")
    if nms:
        h.append(f'<h2 style="font-size:17px;margin:22px 0 8px">Near misses</h2><ul style="padding-left:18px;margin:0;color:{MUTED}">')
        t.append("NEAR MISSES")
        for n in nms:
            h.append(f'<li style="margin:0 0 4px">{a(n.get("url"), (n.get("company") or "") + " — " + (n.get("title") or ""))}: {e(n.get("reason"))}</li>')
            t.append(f"- {n.get('company')} — {n.get('title')}: {n.get('reason')}")
        h.append("</ul>")
    foot = [f'Tracker: {a(TRACKER_URL, "open your tracker")}. Review each resume before applying; set the stage to Applied there, or the next run picks it up from the confirmation email.']
    if d.weekday() == 0:
        watched = [r for r in load(dp("tracker.json"))["rows"] if r.get("status") == "Waiting for opening"]
        if watched:
            foot.append("Still watching: " + "; ".join(e(f"{r['company']} — {r['role']}") for r in watched) + ".")
    if problems:
        foot.append("Run problems: " + "; ".join(e(x) for x in problems))
    es = load(outp("error_summary.json"), {})
    if es.get("flags"):
        foot.append("<b>Needs a look:</b> " + "; ".join(e(x) for x in es["flags"]))
    if es.get("lines"):
        foot.append("Errors this run: " + "; ".join(e(x) for x in es["lines"][:6]))
    h.append(f'<hr style="border:0;border-top:1px solid {LINE};margin:22px 0 10px"><p style="color:{MUTED};font-size:12px;line-height:1.5">' + "<br>".join(foot) + "</p>")
    t.append("\n" + "\n".join(H.unescape(re.sub(r"<[^>]+>", "", x.replace(a(TRACKER_URL, "open your tracker"), TRACKER_URL))) for x in foot))
    html = (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{INK};max-width:680px;line-height:1.45">'
            + "".join(h) + "</div>")
    save(outp("email.json"), {"mode": mode, "subject": subj, "html": html, "text": "\n".join(t), "urgent": urgent})
    print(f"mode={mode} urgent={urgent} subject={subj}")


if __name__ == "__main__":
    main()
