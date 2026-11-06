"""Telemetry: usage capture, cost math, budget guard, privacy, sinks (local file and Supabase REST), report.
Run: python3 tests/test_telemetry.py  (exit 1 on any failure). No network: Supabase is a local fake server."""
import os, sys, json, tempfile, threading, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("CONFIG_DIR", os.path.join(ROOT, "config.example"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import telemetry as T  # noqa: E402

fails, n = [], 0


def check(name, ok, info=""):
    global n
    n += 1
    if not ok:
        fails.append(f"{name}{': ' + str(info) if info else ''}")


PRICES = {"pricing": {"big-model": {"input": 5.0, "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.5},
                      "small-model": {"input": 1.0, "output": 5.0}},
          "web_search_usd_per_1k": 10.0, "pricing_checked_on": "2026-11-01"}
P = T.Pricing.from_dict(PRICES)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def U(i=0, o=0, cw=0, cr=0, ws=0, wf=0, c5=None, c1=None):
    d = {"input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
         "server_tool_use": {"web_search_requests": ws, "web_fetch_requests": wf}}
    if c5 is not None or c1 is not None:
        d["cache_creation"] = {"ephemeral_5m_input_tokens": c5 or 0, "ephemeral_1h_input_tokens": c1 or 0}
    return d


def tel(**kw):
    kw.setdefault("pricing", P)
    return T.Telemetry("2026-11-03", sinks=kw.pop("sinks", [T.MemorySink()]), clock=kw.pop("clock", Clock()),
                       wall=lambda: "2026-11-03T14:00:00+00:00", run_id="00000000-0000-4000-8000-000000000001", **kw)


# ---- usage + cost
u = T.normalize_usage(U(1000, 200, cw=400, cr=3000, ws=2))
check("usage: cache write without breakdown counts as 5-minute", u["cache_write_5m_tokens"] == 400 and u["cache_write_1h_tokens"] == 0, u)
check("usage: missing fields are 0", T.normalize_usage(None) == {k: 0 for k in u})
u2 = T.normalize_usage(U(cw=500, c5=100, c1=400))
check("usage: 5m/1h breakdown kept", (u2["cache_write_5m_tokens"], u2["cache_write_1h_tokens"]) == (100, 400), u2)
# 1000*5 + 200*25 + 400*6.25 + 3000*0.5 = 5000+5000+2500+1500 = 14000 / 1e6 = 0.014 ; + 2 searches * 10/1000 = 0.02
check("cost: tokens + cache + web search", abs(P.cost("big-model", u) - 0.034) < 1e-9, P.cost("big-model", u))
check("cost: dated model id uses its base price", P.cost("big-model-20261101", u) == P.cost("big-model", u))
check("cost: cache prices default from input price", abs(P.cost("small-model", T.normalize_usage(U(cr=1_000_000))) - 0.1) < 1e-9)
check("cost: batch discount on tokens only", abs(P.cost("big-model", u, batch=True) - (0.007 + 0.02)) < 1e-9)
check("cost: unknown model is None", P.cost("mystery", u) is None)
check("cost: search without a search price is None",
      T.Pricing.from_dict({"pricing": PRICES["pricing"]}).cost("big-model", T.normalize_usage(U(10, 10, ws=1))) is None)
check("pricing: fresh table has no problems", P.problems(dt.date(2026, 11, 3)) == [], P.problems(dt.date(2026, 11, 3)))
check("pricing: stale table is flagged", any("re-check" in x for x in P.problems(dt.date(2027, 3, 1))))
check("pricing: missing checked date is flagged",
      any("pricing_checked_on" in x for x in T.Pricing.from_dict({"pricing": PRICES["pricing"]}).problems()))

# ---- recording, steps, budget
clk = Clock()
t = tel(clock=clk, budget_usd=0.10, warn_at=0.5)
with t.step("search"):
    clk.t += 2.5
    t.record("search", "big-model", U(1000, 200, cw=400, cr=3000, ws=2), 1200, stop_reason="end_turn")
t.record("judge", "mystery-model", U(10, 10))
check("record: spent adds priced calls only", abs(t.spent - 0.034) < 1e-9, t.spent)
check("record: unpriced model flagged", any("mystery-model" in f for f in t.flags), t.flags)
check("record: step time measured", t.steps.get("search") == 2.5, t.steps)
t.record("judge", "big-model", U(0, 1000))  # +0.025 -> 0.059 >= 0.05
check("budget: warning at warn_at share", any("of the $0.10 run budget" in f for f in t.flags), t.flags)
try:
    t.check_budget("verify", estimate_usd=0.05)
    check("budget: estimate past budget raises before the call", False)
except T.BudgetExceeded:
    check("budget: estimate past budget raises before the call", True)
check("budget: no budget never raises", tel(budget_usd=None).check_budget("x", 99) is None)

# ---- call wrapper with a duck-typed client (and the real SDK types if installed)


class Resp:
    def __init__(self, usage, model="big-model"):
        self.usage, self.model, self.stop_reason, self.id, self._request_id = usage, model, "end_turn", "msg_1", "req_abc"


FAKE_KEY = "sk-" + "ant-" + "api03-" + "SHOULDNOTLEAK0123456789"  # fictional, built at runtime  gitleaks:allow


class APIErr(Exception):
    def __init__(self, code):
        super().__init__(f"boom {code} key={FAKE_KEY}")
        self.status_code, self.request_id = code, "req_err"


class Client:
    def __init__(self, result):
        self.result = result
        self.messages = self
        self.seen = None

    def create(self, **kw):
        self.seen = kw
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


SECRET_PROMPT = "PRIVATE POSTING TEXT about the candidate jane@example.com"
ms = T.MemorySink()
t = tel(sinks=[ms])
r = t.call(Client(Resp(U(100, 50))), "verify", model="big-model", max_tokens=10, messages=[{"role": "user", "content": SECRET_PROMPT}])
check("call: response returned unchanged", r.id == "msg_1")
check("call: request id kept", t.calls[-1]["request_id"] == "req_abc")
for code, kind in ((429, "rate_limited"), (529, "overloaded"), (401, "auth"), (500, "server_error"), (400, "bad_request")):
    try:
        t.call(Client(APIErr(code)), "verify", model="big-model", messages=[])
        check(f"call: error {code} re-raised", False)
    except APIErr:
        check(f"call: error {code} recorded as {kind}", t.calls[-1]["error_kind"] == kind and not t.calls[-1]["ok"], t.calls[-1])
check("privacy: key-shaped text scrubbed from errors", "SHOULDNOTLEAK" not in json.dumps(t.calls), t.calls[-1]["error"])
check("privacy: prompt text never recorded", SECRET_PROMPT not in json.dumps(ms.calls) and "jane@" not in json.dumps(ms.calls))
check("record: each call streamed to sinks", len(ms.calls) == len(t.calls) == 6)
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "service-role-value-123456"
check("scrub: configured secret values removed", "service-role-value" not in T.scrub("failed with service-role-value-123456"))

try:
    import anthropic, httpx
    from anthropic.types import Message, Usage, ServerToolUsage, CacheCreation
    msg = Message(id="msg_x", type="message", role="assistant", model="big-model-20261101", content=[], stop_reason="end_turn",
                  stop_sequence=None, usage=Usage(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=400,
                                                  cache_read_input_tokens=3000,
                                                  cache_creation=CacheCreation(ephemeral_5m_input_tokens=400, ephemeral_1h_input_tokens=0),
                                                  server_tool_use=ServerToolUsage(web_search_requests=2, web_fetch_requests=0)))
    t = tel()
    t.call(Client(msg), "judge", model="big-model")
    check("SDK: real Message usage is read", abs(t.calls[-1]["cost_usd"] - 0.034) < 1e-9 and t.calls[-1]["model"] == "big-model-20261101", t.calls[-1])
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)
    try:
        t.call(Client(err), "judge", model="big-model")
    except anthropic.RateLimitError:
        check("SDK: RateLimitError classified", t.calls[-1]["error_kind"] == "rate_limited", t.calls[-1])
    try:
        t.call(Client(anthropic.APITimeoutError(request=req)), "judge", model="big-model")
    except anthropic.APITimeoutError:
        check("SDK: timeout classified", t.calls[-1]["error_kind"] == "timeout", t.calls[-1])
except ImportError:
    print("(anthropic SDK or httpx not installed: SDK checks skipped)")

# ---- summary + finish + local files
tmp = tempfile.mkdtemp()
clk = Clock()
t = tel(clock=clk, sinks=[T.JsonlSink(os.path.join(tmp, "telemetry"))], out_dir=os.path.join(tmp, "out"), budget_usd=1.0)
t.record("search", "big-model", U(1000, 200, cw=400, cr=3000, ws=2))
t.record("judge", "big-model", U(2000, 400))
t.count("picks", 2)
clk.t = 61.0
s = t.finish("done", email_mode="full")
check("summary: totals", (s["calls"], s["input_tokens"], s["output_tokens"], s["web_search_requests"]) == (2, 3000, 600, 2), s)
check("summary: cost per pick", s["cost_per_pick_usd"] == round(s["cost_usd"] / 2, 4), s)
check("summary: cache hit rate", s["cache_hit_rate"] == round(3000 / (3000 + 400 + 3000), 3), s["cache_hit_rate"])
check("summary: per-step rollup", set(s["by_step"]) == {"search", "judge"} and s["by_step"]["judge"]["output_tokens"] == 400)
check("summary: duration", s["duration_s"] == 61.0)
check("finish: out/telemetry_summary.json written", json.load(open(os.path.join(tmp, "out", "telemetry_summary.json")))["id"] == s["id"])
check("finish: calls file for the run date", len(open(os.path.join(tmp, "telemetry", "calls-2026-11-03.jsonl")).readlines()) == 2)
check("finish: run line appended", json.loads(open(os.path.join(tmp, "telemetry", "runs.jsonl")).readline())["cost_usd"] == s["cost_usd"])
try:
    tel().summary("finished-ish")
    check("summary: unknown status rejected", False)
except ValueError:
    check("summary: unknown status rejected", True)


class Broken:
    def call(self, rec):
        raise OSError("disk full")

    def run(self, s, c):
        raise OSError("disk full")


t = tel(sinks=[Broken()], out_dir=os.path.join(tmp, "out2"))
t.record("x", "big-model", U(1, 1))
s = t.finish("done")
check("sinks: a failing sink never breaks the run", "telemetry upload incomplete (see the error log)" in s["flags"], s["flags"])
ev = [json.loads(l) for l in open(os.path.join(tmp, "out2", "errors", "telemetry.jsonl"))]
check("sinks: failure logged to out/errors/telemetry.jsonl for errors.py", ev and ev[0]["error_type"] == "telemetry_upload", ev)

rep = T.report(os.path.join(tmp, "telemetry"), 7, today=dt.date(2026, 11, 3))
check("report: per-step table and run line", "search" in rep and "judge" in rep and "2026-11-03" in rep and "total $" in rep, rep)

# ---- Supabase sink against a local fake PostgREST


class Fake(BaseHTTPRequestHandler):
    log = []
    plan = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.log.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        code = Fake.plan.pop(0) if Fake.plan else 201
        self.send_response(code)
        self.end_headers()
        if code >= 400:
            self.wfile.write(b'{"message":"nope"}')

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1"
KEY = "service-role-value-123456"
OWNER = "11111111-1111-4111-8111-111111111111"


def sb_run(plan, chunk=500, ncalls=3):
    Fake.log, Fake.plan = [], list(plan)
    sink = T.SupabaseSink(url, KEY, OWNER, chunk=chunk, sleep=lambda s: None)
    t = tel(sinks=[sink], out_dir=os.path.join(tmp, "out3"))
    for i in range(ncalls):
        t.record("verify", "big-model", U(10, 10))
    return t.finish("done"), t


s, t = sb_run([], chunk=2)
paths = [x["path"] for x in Fake.log]
check("supabase: run first, then calls in chunks, as upserts",
      paths == ["/rest/v1/telemetry_runs?on_conflict=id", "/rest/v1/telemetry_calls?on_conflict=id", "/rest/v1/telemetry_calls?on_conflict=id"], paths)
h = Fake.log[0]["headers"]
check("supabase: service key sent only in auth headers", h.get("apikey") == KEY and h.get("authorization") == f"Bearer {KEY}")
check("supabase: merge-duplicates so retries never duplicate", "merge-duplicates" in h.get("prefer", ""))
check("supabase: every row carries the owner id", Fake.log[0]["body"][0]["user_id"] == OWNER and all(r["user_id"] == OWNER for x in Fake.log[1:] for r in x["body"]))
check("supabase: no problems on success", t.sink_problems == [], t.sink_problems)
s, t = sb_run([503, 201])
check("supabase: 5xx retried", len([x for x in Fake.log if "runs" in x["path"]]) == 2 and t.sink_problems == [], t.sink_problems)
s, t = sb_run([400])
check("supabase: 4xx not retried, calls not sent, run continues",
      len(Fake.log) == 1 and t.sink_problems and "telemetry upload incomplete (see the error log)" in s["flags"], (len(Fake.log), t.sink_problems))
check("supabase: key never in problem text", all(KEY not in p for p in t.sink_problems), t.sink_problems)
sink = T.SupabaseSink("http://127.0.0.1:9", KEY, OWNER, retries=2, timeout=1, sleep=lambda s: None)
check("supabase: unreachable server -> problem, no exception", sink.run({"id": "x"}, []) != [])
srv.shutdown()

# ---- from_config: Supabase sink only when all three env vars are set
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
env_none = {}
env_all = {"SUPABASE_URL": url, "SUPABASE_SERVICE_ROLE_KEY": KEY, "SUPABASE_OWNER_ID": OWNER}
a = T.Telemetry.from_config(env=env_none)
b = T.Telemetry.from_config(env=env_all)
check("from_config: local file sink always; Supabase only with url+key+owner",
      [x.__class__.__name__ for x in a.sinks] == ["JsonlSink"] and [x.__class__.__name__ for x in b.sinks] == ["JsonlSink", "SupabaseSink"])
check("from_config: example pricing is flagged until checked", any("pricing_checked_on" in f for f in a.flags), a.flags)

print(f"{n - len(fails)}/{n} passed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
