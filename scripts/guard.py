"""Run guard: one run per day.

  python3 scripts/guard.py start [--force]   -> exit 0 = go, exit 3 = stop (already ran / running)
  python3 scripts/guard.py status            -> prints today's run-log status

A run marks itself by committing STATE_DIR/runs/<date>.json (in the config repo) with status "started";
the end of the run rewrites it with status "done". A "started" marker older than 2 hours
is treated as a crashed run and may be retried. --force = you said "run anyway".
"""
import sys
import subprocess
import datetime as dt
from common import load, save, today, iso_now, now_et, dp


def git(*args, check=True):
    from config import state_dir  # run state is committed in the config repo, never in the engine
    return subprocess.run(["git", "-C", state_dir(), *args], capture_output=True, text=True, check=check)


def marker():
    return dp("runs", f"{today()}.json")


def existing():
    try:
        return load(marker())
    except FileNotFoundError:
        return None


def blocked(log):
    if not log:
        return None
    st = log.get("status")
    if st in ("done", "done_no_email"):
        return f"already ran today ({st})"
    if st == "started":
        try:
            started = dt.datetime.fromisoformat(log.get("started_at"))
            if (now_et() - started).total_seconds() < 2 * 3600:
                return "another run started less than 2 hours ago"
        except Exception:
            return "another run is marked as started"
    return None


def start(force):
    git("pull", "--rebase", "-q", check=False)
    reason = blocked(existing())
    if reason and not force:
        print(f"STOP: {reason}")
        return 3
    log = existing() or {}
    log.update({"date": today(), "status": "started", "started_at": iso_now(), "forced": bool(force and reason)})
    save(marker(), log)
    git("add", marker())
    git("commit", "-q", "-m", f"run {today()}: started", check=False)
    r = git("push", "-q", check=False)
    if r.returncode != 0:
        # someone else pushed first; re-check
        git("reset", "-q", "--hard", "HEAD~1", check=False)
        git("pull", "--rebase", "-q", check=False)
        reason = blocked(existing())
        if reason and not force:
            print(f"STOP: {reason} (lost the race)")
            return 3
        save(marker(), log)
        git("add", marker())
        git("commit", "-q", "-m", f"run {today()}: started", check=False)
        if git("push", "-q", check=False).returncode != 0:
            print("WARN: could not push the run marker; continuing (note in footer)")
    print("GO")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        sys.exit(start("--force" in sys.argv))
    print((existing() or {}).get("status", "none"))
