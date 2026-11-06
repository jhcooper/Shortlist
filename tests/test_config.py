"""Config layer: the example validates, broken configs fail with readable errors, prompts render completely.
Run: python3 tests/test_config.py  (exit 1 on any failure). Uses only config.example/."""
import os, sys, shutil, tempfile, subprocess, re, glob
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EX = os.path.join(ROOT, "config.example")
fails, n = [], 0


def check(name, ok, info=""):
    global n
    n += 1
    if not ok:
        fails.append(f"{name}{': ' + info if info else ''}")


def run(cfg, *args):
    env = dict(os.environ, CONFIG_DIR=cfg)
    env.pop("STATE_DIR", None)
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "config.py"), *args], capture_output=True, text=True, env=env)


r = run(EX, "check")
check("example config validates", r.returncode == 0, r.stderr.strip())


def broken(edit, expect):
    d = tempfile.mkdtemp()
    shutil.copytree(EX, d, dirs_exist_ok=True)
    edit(d)
    r = run(d, "check")
    shutil.rmtree(d)
    check(f"broken config is reported: {expect}", r.returncode == 1 and expect in r.stderr, r.stderr.strip()[:200])


def sub(fname, old, new):
    def f(d):
        p = os.path.join(d, fname)
        s = open(p, encoding="utf-8").read()
        assert old in s, (fname, old)
        open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
    return f


broken(lambda d: os.remove(os.path.join(d, "sources.yaml")), "sources.yaml is missing")
broken(sub("profile.yaml", "email: alex.rivera@example.com", "email: not-an-email"), "person.email should be an email address")
broken(sub("preferences.yaml", "recipient: alex.rivera@example.com", "recipient: \"\""), "digest.recipient should be an email address")
broken(sub("profile.yaml", "graduation: 2027-05", "graduation: May 2027"), "education.graduation should be a year-month")
broken(sub("preferences.yaml", "  experience_years_required_drop_at: 5", "  experience_years_required_drop_at: five"),
       "experience_years_required_drop_at should be a whole number")
broken(sub("profile.yaml", "  - Built a Python pipeline that cleaned 400K", "  - Built a Python pipeline that tidied 400K"),
       "bullet not in profile.yaml experience")
broken(lambda d: open(os.path.join(d, "preferences.yaml"), "a").write("\n  bad: [unclosed\n"), "not valid YAML")

r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "config.py"), "check"], capture_output=True, text=True,
                   env={k: v for k, v in os.environ.items() if k != "CONFIG_DIR"}, cwd=tempfile.gettempdir())
if not os.path.exists(os.path.join(ROOT, ".config_dir")):
    check("missing CONFIG_DIR is explained", r.returncode == 1 and "CONFIG_DIR is not set" in r.stderr, r.stderr.strip()[:200])

# prompts: every placeholder has a value; nothing left unrendered
env = dict(os.environ, CONFIG_DIR=EX)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["CONFIG_DIR"] = EX
import config  # noqa: E402
vals = config.template_values()
for f in sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md"))):
    out = config.render(open(f, encoding="utf-8").read(), vals)
    check(f"{os.path.basename(f)} renders fully", "{{" not in out)

# the engine itself carries no personal config or run state
for d in ("data", "resumes"):
    check(f"engine has no {d}/ folder", not os.path.exists(os.path.join(ROOT, d)))
check("engine has no config/ folder", not os.path.exists(os.path.join(ROOT, "config")))

print(f"{n - len(fails)}/{n} passed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
