"""Validate (resumes), build the .docx, and check page count.

  python3 scripts/build_doc.py resume <base_key> <spec.json> <out.docx>
  python3 scripts/build_doc.py cover <spec.json> <out.docx>

Prints one JSON line: {"ok", "path", "bytes", "pages", "errors", "warnings"}. Exit 1 if not ok.
A resume must be exactly 1 page; a cover letter at most 1 page. Page count uses LibreOffice
(soffice) + pdfinfo when present; if they are missing, pages = null and the build still passes
with a warning.
"""
import sys, os, json, subprocess, shutil, tempfile
from common import p, load
from validate_resume import validate
from config import load_config


def header_info():
    """Name, contact, links and education for the document header, from profile.yaml (builder.js holds none)."""
    pr = load_config()["profile"]
    person = pr["person"]
    return {"name": person["name"], "location": person.get("home", ""), "phone": person.get("phone", ""), "email": person.get("email", ""),
            "links": person.get("links", []), "education": pr["education"]["resume_lines"]}


def pages(docx):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not shutil.which("pdfinfo"):
        return None
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", d, docx], capture_output=True, timeout=120)
        pdf = os.path.join(d, os.path.splitext(os.path.basename(docx))[0] + ".pdf")
        if not os.path.exists(pdf):
            return None
        out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[1])
    return None


def build(kind, spec_path, out, base_key=None):
    spec_path, out = os.path.abspath(spec_path), os.path.abspath(out)
    res = {"ok": True, "path": out, "errors": [], "warnings": []}
    spec = load(spec_path)
    if kind == "resume":
        v = validate(base_key, spec)
        res["errors"] += v["errors"]
        res["warnings"] += v["warnings"]
        if v["errors"]:
            res["ok"] = False
            return res
        spec["type"] = "resume"
    else:
        spec["type"] = "cover"
        txt = " ".join(spec.get("paragraphs", []))
        if len(txt.split()) > 380:
            res["errors"].append(f"cover letter is {len(txt.split())} words (max ~350)")
            res["ok"] = False
            return res
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    import tempfile as _tf
    with _tf.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as hf:
        json.dump(header_info(), hf, ensure_ascii=False)
    r = subprocess.run(["node", p("scripts", "builder.js"), spec_path, out, hf.name], capture_output=True, text=True, cwd=p())
    os.unlink(hf.name)
    if r.returncode != 0 or not os.path.exists(out):
        res["ok"] = False
        res["errors"].append("builder failed: " + (r.stderr or r.stdout)[-300:])
        return res
    res["bytes"] = os.path.getsize(out)
    n = pages(out)
    res["pages"] = n
    if n is None:
        res["warnings"].append("page count not checked (LibreOffice unavailable)")
    elif n != 1:
        res["ok"] = False
        res["errors"].append(f"{n} pages; shorten the summary or drop the weakest bullet")
    return res


if __name__ == "__main__":
    if sys.argv[1] == "resume":
        r = build("resume", sys.argv[3], sys.argv[4], sys.argv[2])
    else:
        r = build("cover", sys.argv[2], sys.argv[3])
    print(json.dumps(r))
    if r["ok"] and sys.argv[1] == "resume":
        from common import outp
        built = load(outp("built.json"), {}) if os.path.exists(outp("built.json")) else {}
        built[os.path.splitext(os.path.basename(sys.argv[3]))[0]] = os.path.basename(r["path"])
        with open(outp("built.json"), "w", encoding="utf-8") as f:
            json.dump(built, f, ensure_ascii=False)
    if not r["ok"]:
        from errors import log
        log("documents", "validation_failed", os.path.basename(r["path"]), "build_doc", "; ".join(r["errors"])[:300], helper="scripts")
    sys.exit(0 if r["ok"] else 1)
