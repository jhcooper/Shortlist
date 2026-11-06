"""Personal-data check for the engine repo (secrets are gitleaks' job; this catches YOUR details).

Two kinds of rule:
  generic   any email address outside example/placeholder domains; any US phone number outside the 555-01xx fiction range.
  private   your own patterns (name, street, school ids, ...), one regex per line, from the first of:
            --patterns FILE, the LEAK_PATTERNS environment variable (a CI secret), $CONFIG_DIR/leak_patterns.txt.
            They live in your private config, so the public repo never contains them.

A line containing the marker `leakcheck:allow` is skipped (for deliberate fake data in tests).

Output names the file, line and rule only, never the matched text (CI logs of a public repo are public).

  python3 scripts/leak_check.py --staged      files staged for commit (the pre-commit hook)
  python3 scripts/leak_check.py --all         every tracked file (CI, and before making the repo public)
Exit 0 clean, 1 hits, 2 usage/setup error.
"""
import os, re, subprocess, sys

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com", "anthropic.com")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\d.])\(?\b(\d{3})\)?[-. ](\d{3})[-. ](\d{4})\b(?![\d.])")
ALLOW_MARK = "leakcheck:" + "allow"  # put on a line holding deliberate fake data (test fixtures); reviewed in PRs
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx", ".woff", ".woff2", ".zip", ".gz")
SKIP_FILES = ("package-lock.json",)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def files(mode):
    if mode == "--staged":
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split("\n")
        return [(n, lambda n=n: git("show", f":{n}")) for n in names if n]
    names = git("ls-files").split("\n")
    return [(n, lambda n=n: open(n, encoding="utf-8", errors="replace").read()) for n in names if n and os.path.isfile(n)]


def private_patterns(path=None):
    text = None
    if path:
        text = open(path, encoding="utf-8").read()
    elif os.environ.get("LEAK_PATTERNS"):
        text = os.environ["LEAK_PATTERNS"]
    else:
        cd = os.environ.get("CONFIG_DIR")
        if not cd and os.path.exists(".config_dir"):
            cd = open(".config_dir").read().strip()
        if cd and os.path.exists(os.path.join(cd, "leak_patterns.txt")):
            text = open(os.path.join(cd, "leak_patterns.txt"), encoding="utf-8").read()
    if text is None:
        return None
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            try:
                out.append((f"private pattern #{i}", re.compile(line, re.I)))
            except re.error:
                print(f"leak_check: private pattern #{i} is not a valid regex", file=sys.stderr)
                sys.exit(2)
    return out


def scan_text(text, rules):
    """-> [(line_no, rule_name)] without the matched text."""
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for m in EMAIL.finditer(line):
            dom = m.group(0).split("@", 1)[1].lower()
            if not any(dom == d or dom.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
                hits.append((no, "email address outside example domains"))
        for m in PHONE.finditer(line):
            if not (m.group(2) == "555" and m.group(3).startswith("01")):
                hits.append((no, "phone number (use 555-01xx in examples)"))
        for name, rx in rules:
            if rx.search(line):
                hits.append((no, name))
    return hits


def main(argv):
    if not argv or argv[0] not in ("--staged", "--all"):
        print(__doc__, file=sys.stderr)
        return 2
    pat = argv[argv.index("--patterns") + 1] if "--patterns" in argv else None
    rules = private_patterns(pat)
    if rules is None:
        print("leak_check: no private patterns found (set LEAK_PATTERNS or CONFIG_DIR); generic checks only", file=sys.stderr)
        rules = []
    total = 0
    for name, read in files(argv[0]):
        if name.endswith(SKIP_EXT) or os.path.basename(name) in SKIP_FILES:
            continue
        try:
            text = read()
        except (OSError, subprocess.CalledProcessError):
            continue
        for no, rule in scan_text(text, rules):
            print(f"{name}:{no}: {rule}")
            total += 1
    if total:
        print(f"leak_check: {total} possible personal-data hit(s). Move the value to your private config.", file=sys.stderr)
        return 1
    print(f"leak_check: clean ({len(rules)} private patterns + generic checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
