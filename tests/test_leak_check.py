"""leak_check: catches personal emails, phone numbers and private patterns; allows fiction; never prints the match."""
import os
import re
import subprocess
import sys

import pytest

import leak_check as L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "leak_check.py")
RULES = [("private pattern #1", re.compile(r"maple\s+court", 2))]
CASES = [("contact pat.doe@gmail.com", True), ("alex.rivera@example.com", False), ("noreply@anthropic.com", False),  # leakcheck:allow
         ("call 646-273-9184", True), ("phone: 212-555-0142", False), ("(415) 555-0199", False),  # leakcheck:allow
         ("version 1.234.567.8901", False), ("12 Maple Court", True), ("timestamp 2026-11-03", False)]


@pytest.mark.parametrize(("text", "bad"), CASES, ids=[f"{'flags' if b else 'allows'}: {t}" for t, b in CASES])
def test_scan_text(text, bad):
    hits = L.scan_text(text, RULES)
    assert bool(hits) == bad, hits


def test_allow_marker_skips_a_line():
    assert L.scan_text("x pat.doe@gmail.com  # leakcheck:" + "allow", RULES) == []  # leakcheck:allow


# end to end on a throwaway repo: staged mode, exit codes, no matched text in output
@pytest.fixture
def repo(tmp_path):
    def run(*a, **kw):
        return subprocess.run(a, cwd=tmp_path, capture_output=True, text=True, **kw)

    def stage(text):
        (tmp_path / "a.txt").write_text(text)
        run("git", "add", "a.txt")

    run("git", "init", "-q")
    return tmp_path, run, stage


def env_with_pattern():
    env = dict(os.environ, LEAK_PATTERNS="secretword")
    env.pop("CONFIG_DIR", None)
    return env


@pytest.fixture
def staged_email(repo):
    d, run, stage = repo
    (d / "pats.txt").write_text("# comment\nsecretword\n")
    stage("ok line\nemail pat.doe@gmail.com\n")  # leakcheck:allow
    return run(sys.executable, SCRIPT, "--staged", env=env_with_pattern())


def test_staged_hit_exits_1_with_file_line(staged_email):
    r = staged_email
    assert r.returncode == 1 and "a.txt:2:" in r.stdout, r.stdout + r.stderr


def test_output_never_contains_the_matched_text(staged_email):
    assert "pat.doe" not in staged_email.stdout + staged_email.stderr


def test_private_pattern_from_file_named_by_number(repo):
    d, run, stage = repo
    (d / "pats.txt").write_text("# comment\nsecretword\n")
    stage("the secretword is here\n")
    r = run(sys.executable, SCRIPT, "--staged", "--patterns", str(d / "pats.txt"))
    assert r.returncode == 1 and "private pattern #2" in r.stdout and "secretword" not in r.stdout, r.stdout


def test_clean_exits_0(repo):
    d, run, stage = repo
    stage("nothing here\n")
    r = run(sys.executable, SCRIPT, "--staged", env=env_with_pattern())
    assert r.returncode == 0, r.stdout + r.stderr
