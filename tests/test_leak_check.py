"""leak_check: catches personal emails, phone numbers and private patterns; allows fiction; never prints the match.
Run: python3 tests/test_leak_check.py"""
import os, sys, subprocess, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import leak_check as L  # noqa: E402

fails, n = [], 0


def check(name, ok, info=""):
    global n
    n += 1
    if not ok:
        fails.append(f"{name}: {info}")


rules = [("private pattern #1", __import__("re").compile(r"maple\s+court", 2))]
cases = [("contact pat.doe@gmail.com", True), ("alex.rivera@example.com", False), ("noreply@anthropic.com", False),  # leakcheck:allow
         ("call 646-273-9184", True), ("phone: 212-555-0142", False), ("(415) 555-0199", False),  # leakcheck:allow
         ("version 1.234.567.8901", False), ("12 Maple Court", True), ("timestamp 2026-11-03", False)]
for text, bad in cases:
    hits = L.scan_text(text, rules)
    check(f"{'flags' if bad else 'allows'}: {text}", bool(hits) == bad, hits)

# end to end on a throwaway repo: staged mode, exit codes, no matched text in output
check("allow marker skips a line", L.scan_text("x pat.doe@gmail.com  # leakcheck:" + "allow", rules) == [])  # leakcheck:allow
d = tempfile.mkdtemp()
run = lambda *a, **kw: subprocess.run(a, cwd=d, capture_output=True, text=True, **kw)
run("git", "init", "-q")
open(os.path.join(d, "a.txt"), "w").write("ok line\nemail pat.doe@gmail.com\n")  # leakcheck:allow
open(os.path.join(d, "pats.txt"), "w").write("# comment\nsecretword\n")
run("git", "add", "a.txt")
env = dict(os.environ, LEAK_PATTERNS="secretword")
env.pop("CONFIG_DIR", None)
r = run(sys.executable, os.path.join(ROOT, "scripts", "leak_check.py"), "--staged", env=env)
check("staged hit -> exit 1 with file:line", r.returncode == 1 and "a.txt:2:" in r.stdout, r.stdout + r.stderr)
check("output never contains the matched text", "pat.doe" not in r.stdout + r.stderr)
open(os.path.join(d, "a.txt"), "w").write("the secretword is here\n")
run("git", "add", "a.txt")
r = run(sys.executable, os.path.join(ROOT, "scripts", "leak_check.py"), "--staged", "--patterns", os.path.join(d, "pats.txt"))
check("private pattern from file -> exit 1, named by number", r.returncode == 1 and "private pattern #2" in r.stdout
      and "secretword" not in r.stdout, r.stdout)
open(os.path.join(d, "a.txt"), "w").write("nothing here\n")
run("git", "add", "a.txt")
r = run(sys.executable, os.path.join(ROOT, "scripts", "leak_check.py"), "--staged", env=env)
check("clean -> exit 0", r.returncode == 0, r.stdout + r.stderr)

print(f"{n - len(fails)}/{n} passed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
