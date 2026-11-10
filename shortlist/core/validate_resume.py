"""Reject tailored resumes that add facts.

  python3 scripts/validate_resume.py <base_key> <spec.json>      exit 0 ok, 1 errors (printed as JSON)
  python3 scripts/validate_resume.py --docx <file.docx>            checks an existing .docx's text (numbers, tools, GPA)

base_key: a key of CONFIG_DIR/resumes/base_resumes.json
spec.json: a resume object shaped like those base resume entries.
Facts come only from config: profile.yaml (experience, skills.approved, contact, education) and the base resumes.
"""
import sys
import re
import json
import zipfile
from shortlist.common import load
from shortlist.config import load_config

_CFG = load_config()
APPROVED_SKILLS = _CFG["profile"]["skills"]["approved"]
# education.gpa.resume_shows, e.g. "undergrad 3.60 only": the only GPA a resume may print (3.60 and 3.6 both count)
GPA_OK = {g for x in re.findall(r"\d\.\d+", str(_CFG["profile"]["education"]["gpa"].get("resume_shows", ""))) for g in (x, x.rstrip("0").rstrip("."))}


def contact_corpus():
    """Numbers that legitimately appear in the header/education of every resume (phone, link ids, dates, GPA)."""
    pr = _CFG["profile"]
    bits = [pr["person"].get("phone", ""), pr["person"].get("email", ""), *[link["label"] for link in pr["person"].get("links", [])]]
    for e in pr["education"]["resume_lines"]:
        bits += [e["line"], e.get("date", ""), e.get("detail", "")]
    return "\n".join(bits)
# Tools/tech that must never appear unless they are already in the base text or the approved skills.
WATCH_TECH = ["Tableau", "Power BI", "Looker", "Excel", "Spark", "PySpark", "Hadoop", "Snowflake", "Databricks", "dbt",
              "Airflow", "Kafka", "Kubernetes", "Terraform", "Azure", "GCP", "Google Cloud", "Java", "C++", "C#", "Go",
              "Rust", "Scala", "R ", "MATLAB", "Salesforce", "SAP", "Jira", "BigQuery", "Redshift",
              "PostgreSQL", "MySQL", "MongoDB", "NoSQL", "Flask", "Django", "FastAPI", "Node.js", "Angular", "Vue",
              "LangChain", "LLM", "RAG", "ArcGIS", "QGIS", "Selenium", "CI/CD", "Jenkins", "Linux", "Bash"]
ACTION_VERBS = set("""achieved administered analyzed architected assessed audited automated built calculated championed
coached collaborated communicated configured consolidated consulted coordinated created debugged decreased defined delivered
deployed designed developed diagnosed directed documented drove engineered enhanced established evaluated executed expanded
extracted facilitated forecasted founded generated guided identified implemented improved increased initiated integrated
interpreted introduced investigated launched led maintained managed mapped mentored migrated modeled monitored negotiated
optimized orchestrated organized oversaw owned partnered piloted pioneered planned presented prioritized processed produced
programmed prototyped published queried ran reduced refactored remediated reported researched resolved restructured reviewed
scaled scoped scripted secured shipped simplified solved spearheaded standardized streamlined supervised supported surveyed
taught tested trained transformed troubleshot tuned unified upgraded validated visualized wrote""".split())


def base_corpus(bases):
    parts = []
    for b in bases.values():
        parts.append(b.get("summary", ""))
        parts.append(b.get("tagline", ""))
        for j in b["jobs"]:
            parts += [j["org"], j["loc"], j["title"], j["dates"], *j.get("bullets", [])]
        for k, v in b.get("skills", []):
            parts += [k, v]
    return "\n".join(parts)


def numbers(text):
    return set(re.findall(r"\d+(?:\.\d+)?\s*(?:%|M\+?|K\+?|\+)?", text))


def norm_num(n):
    return re.sub(r"\s+", "", n)


def stem(w):
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def words(text):
    return set(re.findall(r"[a-z]+", text.lower()))


def stems(ws):
    return {stem(w) for w in ws}


VERB_STEMS = {stem(v) for v in ACTION_VERBS}


def check_text(text, corpus, errors, where):
    allowed_nums = {norm_num(n) for n in numbers(corpus)} | GPA_OK | {"2027", "2026", "2025", "2024", "2023", "2022", "1", "2"}
    for n in numbers(text):
        nn = norm_num(n)
        if nn not in allowed_nums and nn.rstrip("+%MK") not in {a.rstrip("+%MK") for a in allowed_nums}:
            errors.append(f"{where}: number '{n.strip()}' is not in the base resume")
    for tech in WATCH_TECH:
        pat = r"(?<![A-Za-z])" + re.escape(tech.strip()) + r"(?![A-Za-z+#])"
        if re.search(pat, text) and not re.search(pat, corpus) and tech.strip() not in APPROVED_SKILLS:
            errors.append(f"{where}: mentions '{tech.strip()}', which is not in the base resume or approved skills")
    for m in re.finditer(r"GPA[^0-9]{0,10}(\d\.\d+)", text):
        if m.group(1) not in GPA_OK:
            errors.append(f"{where}: GPA {m.group(1)} - only the GPA in profile.yaml (education.gpa.resume_shows) may appear")


def validate(base_key, spec):
    bases = _CFG["bases"]
    base = bases[base_key]
    corpus = base_corpus(bases)
    errors, warnings = [], []
    summary = spec.get("summary", "")
    nwords = len(summary.split())
    if nwords > 75:
        errors.append(f"summary is {nwords} words (limit ~65, hard 75)")
    elif nwords > 65:
        warnings.append(f"summary is {nwords} words (target 65)")
    check_text(summary + " " + spec.get("tagline", ""), corpus, errors, "summary")
    corpus_words = words(corpus)
    sv = sorted(w for w in words(summary) if stem(w) in VERB_STEMS and stem(w) not in stems(corpus_words))
    if sv:
        errors.append(f"summary claims action(s) not found anywhere in the base resumes: {', '.join(sv)}")
    base_jobs = {(j["org"], j["loc"], j["title"], j["dates"]): j for b in bases.values() for j in b["jobs"]}
    for j in spec.get("jobs", []):
        key = (j.get("org"), j.get("loc"), j.get("title"), j.get("dates"))
        if key not in base_jobs:
            errors.append(f"job header changed or invented: {key}")
            continue
        bl = j.get("bullets", [])
        if len(bl) > 5:
            errors.append(f"{j['org']}: {len(bl)} bullets (max 5)")
        src = [b for bb in bases.values() for jj in bb["jobs"] if (jj["org"], jj["title"]) == (j["org"], j["title"]) for b in jj.get("bullets", [])]
        src_words = words(" ".join(src))
        for b in bl:
            check_text(b, corpus, errors, j["org"])
            new_verbs = sorted(w for w in words(b) if stem(w) in VERB_STEMS and stem(w) not in stems(src_words))
            if new_verbs:
                errors.append(f"{j['org']}: bullet adds action(s) not in the base bullets: {', '.join(new_verbs)} -> \"{b[:80]}\"")
            overlap = len(words(b) & src_words) / max(1, len(words(b)))
            if overlap < 0.5:
                warnings.append(f"{j['org']}: bullet shares little wording with the base bullets ({overlap:.0%}) -> \"{b[:80]}\"")
    names = {(j["org"], j["title"]) for j in spec.get("jobs", [])}
    for j in base["jobs"]:
        if (j["org"], j["title"]) not in names:
            warnings.append(f"dropped job: {j['org']}")
    for label, val in spec.get("skills", []):
        for item in [x.strip() for x in val.split(",") if x.strip()]:
            if item not in APPROVED_SKILLS:
                errors.append(f"skill '{item}' is not on the approved list")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf8")
    return re.sub(r"<[^>]+>", " ", re.sub(r"</w:p>", "\n", xml))


if __name__ == "__main__":
    if sys.argv[1] == "--docx":
        corpus = base_corpus(_CFG["bases"])
        text = docx_text(sys.argv[2])
        errs = []
        for e in _CFG["profile"]["education"]["resume_lines"]:
            text = text.replace(e.get("date", "") or "\0", "")
        check_text(text, corpus + "\n" + contact_corpus(), errs, "docx")
        res = {"ok": not errs, "errors": errs, "warnings": []}
    else:
        res = validate(sys.argv[1], load(sys.argv[2]))
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)
