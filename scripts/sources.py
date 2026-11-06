"""One source vocabulary for every writer (the run's scripts; the tracker page only ever writes "manual").

  source        one of CATEGORIES
  source_detail the original text (for manual rows, without a name: "Added by hand")

normalize_source(raw) -> (category or None, detail). None means "can't map": callers leave the row alone and report it.
"""
import re

CATEGORIES = ("email_alert", "job_board", "career_site", "web_search", "manual")
BOARDS = r"ziprecruiter|dice|indeed|linkedin|glassdoor|built ?in|handshake|jobright|wellfound|simplify|monster|careerbuilder|idealist|usajobs"
_MANUAL = [(r"^added by\b", "Added by hand"), (r"^confirmed open by\b", "Confirmed open by hand"),
           (r"^checked by\b.*\(closed\)", "Checked by hand (closed)"), (r"^checked by\b", "Checked by hand")]


def normalize_source(raw, detail=""):
    s = (raw or "").strip()
    low = s.lower()
    if low in CATEGORIES:                      # already migrated
        return low, detail
    for pat, text in _MANUAL:                  # rows you added: no name in the detail
        if re.search(pat, low):
            return "manual", text
    if re.search(r"\balert\b", low):           # "Indeed alert", "Jobright alert" (email digests)
        return "email_alert", s
    if re.search(r"dream board|career(s)? (site|page)|employer (site|page)|company site", low):
        return "career_site", s
    if re.search(r"\blist\b|new[- ]grad", low): # GitHub new-grad lists are job boards, however they were reached
        return "job_board", s
    if re.search(BOARDS, low):
        return "job_board", s
    if re.search(r"web ?search|^web$|google", low):
        return "web_search", s
    return None, s
