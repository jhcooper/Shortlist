"""Config layer: the example validates, broken configs fail with readable errors, prompts render completely.
Uses only config.example/. Runs the old command `scripts/config.py check` as a subprocess."""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from shortlist import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "config.example")
SCRIPT = os.path.join(ROOT, "scripts", "config.py")
PROMPTS = sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md")))


def run(cfg, *args):
    env = dict(os.environ, CONFIG_DIR=cfg)
    env.pop("STATE_DIR", None)
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)


def test_example_config_validates():
    r = run(EX, "check")
    assert r.returncode == 0, r.stderr.strip()


def sub(fname, old, new):
    def f(d):
        p = os.path.join(d, fname)
        s = open(p, encoding="utf-8").read()
        assert old in s, (fname, old)
        open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))
    return f


def append_bad_yaml(d):
    with open(os.path.join(d, "preferences.yaml"), "a") as f:
        f.write("\n  bad: [unclosed\n")


BROKEN = [
    (lambda d: os.remove(os.path.join(d, "sources.yaml")), "sources.yaml is missing"),
    (sub("profile.yaml", "email: alex.rivera@example.com", "email: not-an-email"), "person.email should be an email address"),
    (sub("preferences.yaml", "recipient: alex.rivera@example.com", "recipient: \"\""), "digest.recipient should be an email address"),
    (sub("profile.yaml", "graduation: 2027-05", "graduation: May 2027"), "education.graduation should be a year-month"),
    (sub("preferences.yaml", "  experience_years_required_drop_at: 5", "  experience_years_required_drop_at: five"),
     "experience_years_required_drop_at should be a whole number"),
    (sub("profile.yaml", "  - Built a Python pipeline that cleaned 400K", "  - Built a Python pipeline that tidied 400K"),
     "bullet not in profile.yaml experience"),
    (append_bad_yaml, "not valid YAML"),
]


@pytest.mark.parametrize(("edit", "expect"), BROKEN, ids=[e for _, e in BROKEN])
def test_broken_config_is_reported(edit, expect, tmp_path):
    d = str(tmp_path / "cfg")
    shutil.copytree(EX, d)
    edit(d)
    r = run(d, "check")
    assert r.returncode == 1 and expect in r.stderr, r.stderr.strip()[:200]


@pytest.mark.skipif(os.path.exists(os.path.join(ROOT, ".config_dir")), reason="a .config_dir file supplies the folder")
def test_missing_config_dir_is_explained():
    r = subprocess.run([sys.executable, SCRIPT, "check"], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "CONFIG_DIR"}, cwd=tempfile.gettempdir())
    assert r.returncode == 1 and "CONFIG_DIR is not set" in r.stderr, r.stderr.strip()[:200]


# prompts: every placeholder has a value; nothing left unrendered
@pytest.mark.parametrize("f", PROMPTS, ids=[f"{os.path.basename(f)} renders fully" for f in PROMPTS])
def test_prompt_renders_fully(f, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", EX)
    out = config.render(open(f, encoding="utf-8").read(), config.template_values())
    assert "{{" not in out


# the engine itself carries no personal config or run state
@pytest.mark.parametrize("d", ["data", "resumes", "config"])
def test_engine_has_no_personal_folder(d):
    assert not os.path.exists(os.path.join(ROOT, d))
