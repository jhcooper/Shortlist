"""Run telemetry for the API version of the engine: tokens, cost, time and outcome of every model call, rolled up per
step and per run, with a per-run budget guard.

What is recorded: run id, step name, model id, token counts (input, output, cache write/read), web search and fetch
counts, latency, stop reason, error kind, request id, and the estimated cost. What is NEVER recorded: prompt or response
text, posting or candidate data, email addresses, keys. Error messages are scrubbed of anything key-shaped and cut short.

Where it goes: only to storage you own. Always STATE_DIR/telemetry/ (committed with run state); also your own Supabase
project when SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_OWNER_ID are set (tables in
supabase/migrations/0001_telemetry.sql). Nothing is sent to the engine's authors or anyone else.

Use from the runner:
    tel = Telemetry.from_config(mode="full")
    with tel.step("judge"):
        resp = tel.call(client, "judge", model=..., max_tokens=..., messages=[...])   # budget-checked, timed, recorded
    tel.count("picks", 3)
    summary = tel.finish("done", email_mode="full")   # writes sinks + out/telemetry_summary.json (finalize.py runlog reads it)

CLI:
    python3 telemetry.py report [--days 7]   per-step and per-day table from STATE_DIR/telemetry (the weekly review uses it)
    python3 telemetry.py pricing             shows the price table in use and whether it needs re-checking
"""
import sys, os, json, time, re, uuid, glob, datetime as dt
import urllib.request, urllib.error
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCHEMA_VERSION = 1
PRICE_FIELDS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
PRICING_STALE_DAYS = 90
STATUSES = ("done", "done_no_email", "stopped", "failed", "budget_reached", "dry_run")  # matches the SQL check


class BudgetExceeded(RuntimeError):
    """Raised BEFORE a model call that would start after the run's budget is spent. The runner stops calling models,
    finishes with what it has, and the email says the run hit its budget."""


# ---------------------------------------------------------------- helpers
_KEYISH = re.compile(r"(sk-ant-[A-Za-z0-9_\-]+|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+|"
                     r"(?i:bearer)\s+[A-Za-z0-9_\-\.]{12,}|[A-Za-z0-9_\-]{40,})")


def scrub(text, limit=200):
    """Remove anything key- or token-shaped and cut to `limit` characters."""
    s = _KEYISH.sub("[redacted]", str(text or ""))
    for name in ("ANTHROPIC_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        v = os.environ.get(name)
        if v and len(v) >= 8:
            s = s.replace(v, "[redacted]")
    return s[:limit]


def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage):
    """Anthropic Messages `usage` (SDK object or dict) -> flat dict of ints. Unknown/missing fields count as 0."""
    cc = _get(usage, "cache_creation")
    cw_total = _int(_get(usage, "cache_creation_input_tokens"))
    cw_5m, cw_1h = _int(_get(cc, "ephemeral_5m_input_tokens")), _int(_get(cc, "ephemeral_1h_input_tokens"))
    if cw_total and not (cw_5m or cw_1h):
        cw_5m = cw_total  # no breakdown given: the default cache lifetime is 5 minutes
    stu = _get(usage, "server_tool_use")
    return {
        "input_tokens": _int(_get(usage, "input_tokens")),
        "output_tokens": _int(_get(usage, "output_tokens")),
        "cache_write_5m_tokens": cw_5m,
        "cache_write_1h_tokens": cw_1h,
        "cache_read_tokens": _int(_get(usage, "cache_read_input_tokens")),
        "web_search_requests": _int(_get(stu, "web_search_requests")),
        "web_fetch_requests": _int(_get(stu, "web_fetch_requests")),
    }


# ---------------------------------------------------------------- pricing
class Pricing:
    """USD prices from preferences.yaml `telemetry.pricing` (per million tokens) plus per-1000 web search price.
    Prices change, so they live in YOUR config with the date you last checked them, never in the engine."""

    def __init__(self, models=None, web_search_per_1k=None, web_fetch_per_1k=0.0, batch_multiplier=0.5, checked_on=None):
        self.models = {k: dict(v) for k, v in (models or {}).items()}
        self.web_search_per_1k = web_search_per_1k
        self.web_fetch_per_1k = web_fetch_per_1k or 0.0
        self.batch_multiplier = batch_multiplier
        self.checked_on = checked_on

    @classmethod
    def from_dict(cls, t):
        t = t or {}
        return cls(t.get("pricing") or {}, t.get("web_search_usd_per_1k"), t.get("web_fetch_usd_per_1k", 0.0),
                   t.get("batch_multiplier", 0.5), str(t["pricing_checked_on"]) if t.get("pricing_checked_on") else None)

    def problems(self, today=None):
        out = []
        for m, p in self.models.items():
            for f in ("input", "output"):
                if not isinstance(p.get(f), (int, float)):
                    out.append(f"telemetry.pricing.{m}.{f} is missing (expected USD per million tokens)")
        today = today or dt.date.today()
        if self.models:
            if not self.checked_on:
                out.append("telemetry.pricing_checked_on is missing: add the date you copied prices from the pricing page")
            else:
                try:
                    age = (today - dt.date.fromisoformat(self.checked_on)).days
                    if age > PRICING_STALE_DAYS:
                        out.append(f"prices were last checked {age} days ago ({self.checked_on}); re-check them")
                except ValueError:
                    out.append("telemetry.pricing_checked_on should be a date like 2026-11-02")
        return out

    def for_model(self, model):
        """Exact id, else the longest configured id that the model id starts with (dated snapshots)."""
        if not model:
            return None
        if model in self.models:
            return self.models[model]
        best = max((k for k in self.models if model.startswith(k)), key=len, default=None)
        return self.models.get(best)

    def cost(self, model, u, batch=False):
        """-> USD float, or None when the model has no price (counted, flagged, and left out of the budget)."""
        p = self.for_model(model)
        if p is None or not isinstance(p.get("input"), (int, float)) or not isinstance(p.get("output"), (int, float)):
            return None
        inp, out = p["input"], p["output"]
        cw5 = p.get("cache_write_5m", inp * 1.25)
        cw1 = p.get("cache_write_1h", inp * 2.0)
        cr = p.get("cache_read", inp * 0.1)
        tok = (u["input_tokens"] * inp + u["output_tokens"] * out + u["cache_write_5m_tokens"] * cw5 +
               u["cache_write_1h_tokens"] * cw1 + u["cache_read_tokens"] * cr) / 1e6
        if batch:
            tok *= self.batch_multiplier
        tools = 0.0
        if u["web_search_requests"]:
            if self.web_search_per_1k is None:
                return None
            tools += u["web_search_requests"] * self.web_search_per_1k / 1000
        tools += u["web_fetch_requests"] * self.web_fetch_per_1k / 1000
        return round(tok + tools, 6)


# ---------------------------------------------------------------- sinks
class MemorySink:
    def __init__(self):
        self.calls, self.runs = [], []

    def call(self, rec):
        self.calls.append(rec)

    def run(self, summary, calls):
        self.runs.append(summary)
        return []


class JsonlSink:
    """Calls are appended as they happen (a crash keeps everything up to it); the run summary goes to runs.jsonl."""

    def __init__(self, folder):
        self.folder = folder

    def _append(self, name, rec):
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def call(self, rec):
        self._append(f"calls-{rec['run_date']}.jsonl", rec)

    def run(self, summary, calls):
        self._append("runs.jsonl", summary)
        return []


class SupabaseSink:
    """Writes the run and its calls to your Supabase project at the end of the run (one upsert for the run, calls in
    chunks). Upserts on primary keys, so a retried upload never duplicates. Uses the service-role key (server side
    only). Never raises: returns a list of scrubbed problem strings for the error log."""

    def __init__(self, url, key, owner_id, timeout=15, retries=3, chunk=500, sleep=time.sleep):
        self.url, self.key, self.owner = url.rstrip("/"), key, owner_id
        self.timeout, self.retries, self.chunk, self.sleep = timeout, retries, chunk, sleep

    def call(self, rec):
        pass  # buffered by Telemetry; sent in run()

    def _post(self, table, rows, conflict):
        body = json.dumps(rows).encode()
        req = urllib.request.Request(
            f"{self.url}/rest/v1/{table}?on_conflict={conflict}", data=body, method="POST",
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"})
        last = ""
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    if 200 <= r.status < 300:
                        return None
                    last = f"HTTP {r.status}"
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf-8", "replace")
                last = f"HTTP {e.code} {detail}"
                if 400 <= e.code < 500 and e.code != 429:
                    break  # a bad request will not succeed on retry
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{e.__class__.__name__}"
            self.sleep(min(2 ** attempt, 8))
        return scrub(f"{table}: {last}")

    def run(self, summary, calls):
        problems = []
        row = dict(summary, user_id=self.owner)
        err = self._post("telemetry_runs", [row], "id")
        if err:
            return [err]  # calls reference the run; no point sending them
        rows = [dict(c, user_id=self.owner) for c in calls]
        for i in range(0, len(rows), self.chunk):
            err = self._post("telemetry_calls", rows[i:i + self.chunk], "id")
            if err:
                problems.append(err)
                break
        return problems


# ---------------------------------------------------------------- telemetry
def _classify(exc):
    code = _get(exc, "status_code")
    name = exc.__class__.__name__.lower()
    if code == 429 or "ratelimit" in name:
        return "rate_limited"
    if code == 529 or "overloaded" in name:
        return "overloaded"
    if "timeout" in name:
        return "timeout"
    if code in (401, 403) or "authentication" in name or "permission" in name:
        return "auth"
    if "connection" in name:
        return "connection"
    if code == 400 or "badrequest" in name:
        return "bad_request"
    if isinstance(code, int) and code >= 500:
        return "server_error"
    return "other"


class Telemetry:
    def __init__(self, run_date, mode="full", sinks=None, pricing=None, budget_usd=None, warn_at=0.8,
                 engine_version="", run_id=None, clock=time.monotonic, wall=None, out_dir=None):
        self.run_id = run_id or str(uuid.uuid4())
        self.run_date, self.mode = run_date, mode
        self.sinks = sinks if sinks is not None else [MemorySink()]
        self.pricing = pricing or Pricing()
        self.budget, self.warn_at = budget_usd, warn_at
        self.engine_version = engine_version
        self.clock = clock
        self.wall = wall or (lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        self.out_dir = out_dir
        self.started_at = self.wall()
        self._t0 = clock()
        self.calls, self.counts, self.steps, self.flags = [], {}, {}, []
        self.spent = 0.0
        self.unpriced = set()
        self._warned = False
        self._seq = 0
        self.sink_problems = []

    # ---- construction from config + environment
    @classmethod
    def from_config(cls, mode="full", env=None, **kw):
        from config import load_config, state_dir
        from common import today, outp
        env = os.environ if env is None else env
        t = (load_config()["preferences"].get("telemetry") or {})
        pricing = Pricing.from_dict(t)
        sinks = [JsonlSink(os.path.join(state_dir(), "telemetry"))]
        url, key, owner = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_ROLE_KEY"), env.get("SUPABASE_OWNER_ID")
        if t.get("supabase", True) and url and key and owner:
            sinks.append(SupabaseSink(url, key, owner))
        tel = cls(today(), mode, sinks, pricing, t.get("budget_usd_per_run"), t.get("warn_at_fraction", 0.8),
                  engine_version=_engine_version(), out_dir=outp(), **kw)
        for p in pricing.problems():
            tel.flag(p)
        return tel

    # ---- recording
    def flag(self, text):
        if text not in self.flags:
            self.flags.append(text)

    def count(self, name, n=1):
        self.counts[name] = self.counts.get(name, 0) + n

    @contextmanager
    def step(self, name):
        t = self.clock()
        try:
            yield
        finally:
            self.steps[name] = round(self.steps.get(name, 0.0) + (self.clock() - t), 3)

    def check_budget(self, step, estimate_usd=0.0):
        if self.budget is None:
            return
        if self.spent + (estimate_usd or 0.0) >= self.budget:
            self.flag(f"budget reached (${self.budget:.2f}) before step {step}; remaining model calls skipped")
            raise BudgetExceeded(f"run budget ${self.budget:.2f} reached at ${self.spent:.2f} before {step}")

    def record(self, step, model, usage=None, latency_ms=0, stop_reason=None, ok=True, error_kind=None,
               error=None, request_id=None, batch=False):
        u = normalize_usage(usage)
        cost = self.pricing.cost(model, u, batch=batch)
        if cost is None and any(u.values()):
            self.unpriced.add(model or "unknown")
            self.flag(f"no price for {model or 'unknown model'} (or web search) in telemetry.pricing; its cost is not counted")
        self._seq += 1
        rec = {"id": f"{self.run_id}:{self._seq:04d}", "run_id": self.run_id, "seq": self._seq, "run_date": self.run_date,
               "at": self.wall(), "step": str(step)[:40], "model": str(model or "")[:80], **u,
               "latency_ms": int(latency_ms or 0), "stop_reason": (stop_reason or None), "ok": bool(ok),
               "error_kind": error_kind, "error": scrub(error) if error else None,
               "request_id": str(request_id)[:80] if request_id else None, "batch": bool(batch),
               "cost_usd": cost, "schema_version": SCHEMA_VERSION}
        self.calls.append(rec)
        if cost:
            self.spent = round(self.spent + cost, 6)
        if self.budget and not self._warned and self.spent >= self.warn_at * self.budget:
            self._warned = True
            self.flag(f"used {self.spent / self.budget:.0%} of the ${self.budget:.2f} run budget by step {step}")
        for s in self.sinks:
            try:
                s.call(rec)
            except Exception as e:  # telemetry must never break the run
                self.sink_problems.append(scrub(f"{s.__class__.__name__}: {e}"))
        return rec

    def call(self, client, step, estimate_usd=0.0, batch=False, **kwargs):
        """client.messages.create(**kwargs), budget-checked first, timed and recorded. Re-raises API errors after
        recording them, so the runner's own retry/skip logic still applies."""
        self.check_budget(step, estimate_usd)
        model = kwargs.get("model")
        t = self.clock()
        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            self.record(step, model, None, (self.clock() - t) * 1000, ok=False, error_kind=_classify(e),
                        error=f"{e.__class__.__name__}: {e}", request_id=_get(e, "request_id"))
            raise
        rid = _get(resp, "_request_id") or _get(resp, "id")
        self.record(step, _get(resp, "model") or model, _get(resp, "usage"), (self.clock() - t) * 1000,
                    stop_reason=_get(resp, "stop_reason"), request_id=rid, batch=batch)
        return resp

    # ---- end of run
    def summary(self, status, email_mode=None):
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        tot = {k: sum(c[k] for c in self.calls) for k in
               ("input_tokens", "output_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "cache_read_tokens",
                "web_search_requests", "web_fetch_requests")}
        by_step, by_model = {}, {}
        for c in self.calls:
            for key, bucket in ((c["step"], by_step), (c["model"], by_model)):
                b = bucket.setdefault(key, {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
                                            "cache_read_tokens": 0, "web_search_requests": 0, "cost_usd": 0.0, "latency_ms": 0})
                b["calls"] += 1
                b["errors"] += 0 if c["ok"] else 1
                for k in ("input_tokens", "output_tokens", "cache_read_tokens", "web_search_requests", "latency_ms"):
                    b[k] += c[k]
                b["cost_usd"] = round(b["cost_usd"] + (c["cost_usd"] or 0.0), 6)
        errs = {}
        for c in self.calls:
            if not c["ok"]:
                errs[c["error_kind"]] = errs.get(c["error_kind"], 0) + 1
        picks = self.counts.get("picks", 0)
        cached_in = tot["cache_read_tokens"]
        all_in = tot["input_tokens"] + tot["cache_write_5m_tokens"] + tot["cache_write_1h_tokens"] + cached_in
        return {
            "id": self.run_id, "run_date": self.run_date, "started_at": self.started_at, "finished_at": self.wall(),
            "duration_s": round(self.clock() - self._t0, 1), "status": status, "mode": self.mode, "email_mode": email_mode,
            "engine_version": self.engine_version, "calls": len(self.calls), **tot,
            "cache_hit_rate": round(cached_in / all_in, 3) if all_in else None,
            "cost_usd": round(self.spent, 4), "cost_complete": not self.unpriced, "unpriced_models": sorted(self.unpriced),
            "budget_usd": self.budget, "cost_per_pick_usd": round(self.spent / picks, 4) if picks else None,
            "counts": dict(self.counts), "step_seconds": dict(self.steps), "by_step": by_step, "by_model": by_model,
            "error_counts": errs, "flags": list(self.flags), "schema_version": SCHEMA_VERSION,
        }

    def finish(self, status, email_mode=None):
        s = self.summary(status, email_mode)
        for sink in self.sinks:
            try:
                self.sink_problems += sink.run(s, self.calls) or []
            except Exception as e:
                self.sink_problems.append(scrub(f"{sink.__class__.__name__}: {e}"))
        if self.sink_problems:
            s["flags"].append("telemetry upload incomplete (see the error log)")
            if self.out_dir:  # out/errors/telemetry.jsonl, collected by errors.py like every helper's log
                try:
                    from errors import event
                    os.makedirs(os.path.join(self.out_dir, "errors"), exist_ok=True)
                    with open(os.path.join(self.out_dir, "errors", "telemetry.jsonl"), "a", encoding="utf-8") as f:
                        for p in self.sink_problems:
                            f.write(json.dumps(event("telemetry", "other", "telemetry", "telemetry_upload", p,
                                                     helper="telemetry")) + "\n")
                except Exception:
                    pass
        if self.out_dir:
            os.makedirs(self.out_dir, exist_ok=True)
            with open(os.path.join(self.out_dir, "telemetry_summary.json"), "w", encoding="utf-8") as f:
                json.dump(s, f, indent=1)
        return s


def _engine_version():
    try:
        import subprocess
        return subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- report
def report(folder, days=7, today=None):
    today = today or dt.date.today()
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    calls = []
    for f in sorted(glob.glob(os.path.join(folder, "calls-*.jsonl"))):
        if os.path.basename(f)[6:16] < since:
            continue
        with open(f, encoding="utf-8") as fh:
            calls += [json.loads(l) for l in fh if l.strip()]
    runs = []
    rp = os.path.join(folder, "runs.jsonl")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as fh:
            runs = [r for r in (json.loads(l) for l in fh if l.strip()) if r.get("run_date", "") >= since]
    lines = [f"Telemetry, last {days} days (since {since}): {len(runs)} runs, {len(calls)} model calls", ""]
    steps = {}
    for c in calls:
        b = steps.setdefault(c["step"], [0, 0, 0, 0, 0, 0.0, 0, 0])
        b[0] += 1; b[1] += c["input_tokens"]; b[2] += c["output_tokens"]; b[3] += c["cache_read_tokens"]
        b[4] += c["web_search_requests"]; b[5] += c["cost_usd"] or 0.0; b[6] += c["latency_ms"]; b[7] += 0 if c["ok"] else 1
    n = max(len(runs), 1)
    lines.append(f"{'step':<14}{'calls':>6}{'in/run':>10}{'out/run':>9}{'cached/run':>11}{'search':>7}{'$/run':>8}{'avg s':>7}{'err':>5}")
    for name, b in sorted(steps.items(), key=lambda kv: -kv[1][5]):
        lines.append(f"{name:<14}{b[0]:>6}{b[1] // n:>10}{b[2] // n:>9}{b[3] // n:>11}{b[4]:>7}{b[5] / n:>8.3f}"
                     f"{b[6] / max(b[0], 1) / 1000:>7.1f}{b[7]:>5}")
    lines.append("")
    lines.append(f"{'date':<12}{'status':<14}{'calls':>6}{'cost $':>8}{'picks':>6}{'$/pick':>8}{'cache hit':>10}  flags")
    for r in runs:
        cpp = r.get("cost_per_pick_usd")
        chr_ = r.get("cache_hit_rate")
        lines.append(f"{r['run_date']:<12}{r['status']:<14}{r['calls']:>6}{r['cost_usd']:>8.2f}"
                     f"{r.get('counts', {}).get('picks', 0):>6}{(f'{cpp:.2f}' if cpp else '-'):>8}"
                     f"{(f'{chr_:.0%}' if chr_ is not None else '-'):>10}  {'; '.join(r.get('flags', []))[:80]}")
    if runs:
        tot = sum(r["cost_usd"] for r in runs)
        lines += ["", f"total ${tot:.2f}; average ${tot / len(runs):.2f}/run"
                  + ("" if all(r.get("cost_complete", True) for r in runs) else " (some calls unpriced: see flags)")]
    return "\n".join(lines)


def main(argv):
    if not argv or argv[0] not in ("report", "pricing"):
        sys.exit(__doc__)
    from config import load_config, state_dir
    if argv[0] == "report":
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
        print(report(os.path.join(state_dir(), "telemetry"), days))
    else:
        p = Pricing.from_dict(load_config()["preferences"].get("telemetry") or {})
        print(json.dumps({"models": p.models, "web_search_usd_per_1k": p.web_search_per_1k,
                          "checked_on": p.checked_on}, indent=1))
        for x in p.problems() or ["prices look complete and recent"]:
            print("-", x)


if __name__ == "__main__":
    main(sys.argv[1:])
