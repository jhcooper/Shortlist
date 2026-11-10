"""Telemetry: usage capture, cost math, budget guard, privacy, sinks (local file and Supabase REST), report.
No network: Supabase is a local fake server."""
import datetime as dt
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import telemetry as T

PRICES = {"pricing": {"big-model": {"input": 5.0, "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.5},
                      "small-model": {"input": 1.0, "output": 5.0}},
          "web_search_usd_per_1k": 10.0, "pricing_checked_on": "2026-11-01"}
P = T.Pricing.from_dict(PRICES)
INCOMPLETE = "telemetry upload incomplete (see the error log)"


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


def test_usage_cache_write_without_breakdown_counts_as_5_minute():
    assert u["cache_write_5m_tokens"] == 400 and u["cache_write_1h_tokens"] == 0, u


def test_usage_missing_fields_are_0():
    assert T.normalize_usage(None) == {k: 0 for k in u}


def test_usage_5m_1h_breakdown_kept():
    u2 = T.normalize_usage(U(cw=500, c5=100, c1=400))
    assert (u2["cache_write_5m_tokens"], u2["cache_write_1h_tokens"]) == (100, 400), u2


def test_cost_tokens_cache_and_web_search():
    # 1000*5 + 200*25 + 400*6.25 + 3000*0.5 = 5000+5000+2500+1500 = 14000 / 1e6 = 0.014 ; + 2 searches * 10/1000 = 0.02
    assert abs(P.cost("big-model", u) - 0.034) < 1e-9, P.cost("big-model", u)


def test_cost_dated_model_id_uses_its_base_price():
    assert P.cost("big-model-20261101", u) == P.cost("big-model", u)


def test_cost_cache_prices_default_from_input_price():
    assert abs(P.cost("small-model", T.normalize_usage(U(cr=1_000_000))) - 0.1) < 1e-9


def test_cost_batch_discount_on_tokens_only():
    assert abs(P.cost("big-model", u, batch=True) - (0.007 + 0.02)) < 1e-9


def test_cost_unknown_model_is_none():
    assert P.cost("mystery", u) is None


def test_cost_search_without_a_search_price_is_none():
    assert T.Pricing.from_dict({"pricing": PRICES["pricing"]}).cost("big-model", T.normalize_usage(U(10, 10, ws=1))) is None


def test_pricing_fresh_table_has_no_problems():
    assert P.problems(dt.date(2026, 11, 3)) == [], P.problems(dt.date(2026, 11, 3))


def test_pricing_stale_table_is_flagged():
    assert any("re-check" in x for x in P.problems(dt.date(2027, 3, 1)))


def test_pricing_missing_checked_date_is_flagged():
    assert any("pricing_checked_on" in x for x in T.Pricing.from_dict({"pricing": PRICES["pricing"]}).problems())


# ---- recording, steps, budget
@pytest.fixture(scope="module")
def budget_run():
    """Two calls (one priced, one unpriced), a snapshot, then a third call that crosses the warning line."""
    clk = Clock()
    t = tel(clock=clk, budget_usd=0.10, warn_at=0.5)
    with t.step("search"):
        clk.t += 2.5
        t.record("search", "big-model", U(1000, 200, cw=400, cr=3000, ws=2), 1200, stop_reason="end_turn")
    t.record("judge", "mystery-model", U(10, 10))
    snap = {"spent": t.spent, "flags": list(t.flags), "steps": dict(t.steps)}
    t.record("judge", "big-model", U(0, 1000))  # +0.025 -> 0.059 >= 0.05
    return t, snap


def test_record_spent_adds_priced_calls_only(budget_run):
    assert abs(budget_run[1]["spent"] - 0.034) < 1e-9, budget_run[1]["spent"]


def test_record_unpriced_model_flagged(budget_run):
    assert any("mystery-model" in f for f in budget_run[1]["flags"]), budget_run[1]["flags"]


def test_record_step_time_measured(budget_run):
    assert budget_run[1]["steps"].get("search") == 2.5, budget_run[1]["steps"]


def test_budget_warning_at_warn_at_share(budget_run):
    assert any("of the $0.10 run budget" in f for f in budget_run[0].flags), budget_run[0].flags


def test_budget_estimate_past_budget_raises_before_the_call(budget_run):
    with pytest.raises(T.BudgetExceeded):
        budget_run[0].check_budget("verify", estimate_usd=0.05)


def test_budget_no_budget_never_raises():
    assert tel(budget_usd=None).check_budget("x", 99) is None


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
ERRORS = [(429, "rate_limited"), (529, "overloaded"), (401, "auth"), (500, "server_error"), (400, "bad_request")]


@pytest.fixture(scope="module")
def wrapped():
    """One successful call carrying private prompt text, then one failing call per API error code."""
    ms = T.MemorySink()
    t = tel(sinks=[ms])
    r = t.call(Client(Resp(U(100, 50))), "verify", model="big-model", max_tokens=10, messages=[{"role": "user", "content": SECRET_PROMPT}])
    first = dict(t.calls[-1])
    errors = {}
    for code, _ in ERRORS:
        try:
            t.call(Client(APIErr(code)), "verify", model="big-model", messages=[])
            errors[code] = None  # not re-raised
        except APIErr:
            errors[code] = dict(t.calls[-1])
    return {"t": t, "ms": ms, "r": r, "first": first, "errors": errors}


def test_call_response_returned_unchanged(wrapped):
    assert wrapped["r"].id == "msg_1"


def test_call_request_id_kept(wrapped):
    assert wrapped["first"]["request_id"] == "req_abc"


@pytest.mark.parametrize(("code", "kind"), ERRORS, ids=[f"error {c} recorded as {k}" for c, k in ERRORS])
def test_call_error_reraised_and_recorded(wrapped, code, kind):
    rec = wrapped["errors"][code]
    assert rec is not None, f"error {code} not re-raised"
    assert rec["error_kind"] == kind and not rec["ok"], rec


def test_privacy_key_shaped_text_scrubbed_from_errors(wrapped):
    assert "SHOULDNOTLEAK" not in json.dumps(wrapped["t"].calls), wrapped["t"].calls[-1]["error"]


def test_privacy_prompt_text_never_recorded(wrapped):
    assert SECRET_PROMPT not in json.dumps(wrapped["ms"].calls) and "jane@" not in json.dumps(wrapped["ms"].calls)


def test_record_each_call_streamed_to_sinks(wrapped):
    assert len(wrapped["ms"].calls) == len(wrapped["t"].calls) == 6


def test_scrub_configured_secret_values_removed(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-value-123456")
    assert "service-role-value" not in T.scrub("failed with service-role-value-123456")


# Real SDK types: needs anthropic<1, which still ships httpx (as in CI).
@pytest.fixture(scope="module")
def sdk():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    return anthropic, httpx, httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_sdk_real_message_usage_is_read(sdk):
    from anthropic.types import CacheCreation, Message, ServerToolUsage, Usage
    msg = Message(id="msg_x", type="message", role="assistant", model="big-model-20261101", content=[], stop_reason="end_turn",
                  stop_sequence=None, usage=Usage(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=400,
                                                  cache_read_input_tokens=3000,
                                                  cache_creation=CacheCreation(ephemeral_5m_input_tokens=400, ephemeral_1h_input_tokens=0),
                                                  server_tool_use=ServerToolUsage(web_search_requests=2, web_fetch_requests=0)))
    t = tel()
    t.call(Client(msg), "judge", model="big-model")
    assert abs(t.calls[-1]["cost_usd"] - 0.034) < 1e-9 and t.calls[-1]["model"] == "big-model-20261101", t.calls[-1]


def test_sdk_rate_limit_error_classified(sdk):
    anthropic, httpx, req = sdk
    err = anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)
    t = tel()
    with pytest.raises(anthropic.RateLimitError):
        t.call(Client(err), "judge", model="big-model")
    assert t.calls[-1]["error_kind"] == "rate_limited", t.calls[-1]


def test_sdk_timeout_classified(sdk):
    anthropic, _, req = sdk
    t = tel()
    with pytest.raises(anthropic.APITimeoutError):
        t.call(Client(anthropic.APITimeoutError(request=req)), "judge", model="big-model")
    assert t.calls[-1]["error_kind"] == "timeout", t.calls[-1]


# ---- summary + finish + local files
@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return str(tmp_path_factory.mktemp("telemetry"))


@pytest.fixture(scope="module")
def finished(tmp):
    clk = Clock()
    t = tel(clock=clk, sinks=[T.JsonlSink(os.path.join(tmp, "telemetry"))], out_dir=os.path.join(tmp, "out"), budget_usd=1.0)
    t.record("search", "big-model", U(1000, 200, cw=400, cr=3000, ws=2))
    t.record("judge", "big-model", U(2000, 400))
    t.count("picks", 2)
    clk.t = 61.0
    return t.finish("done", email_mode="full")


def test_summary_totals(finished):
    s = finished
    assert (s["calls"], s["input_tokens"], s["output_tokens"], s["web_search_requests"]) == (2, 3000, 600, 2), s


def test_summary_cost_per_pick(finished):
    assert finished["cost_per_pick_usd"] == round(finished["cost_usd"] / 2, 4), finished


def test_summary_cache_hit_rate(finished):
    assert finished["cache_hit_rate"] == round(3000 / (3000 + 400 + 3000), 3), finished["cache_hit_rate"]


def test_summary_per_step_rollup(finished):
    assert set(finished["by_step"]) == {"search", "judge"} and finished["by_step"]["judge"]["output_tokens"] == 400


def test_summary_duration(finished):
    assert finished["duration_s"] == 61.0


def test_finish_writes_telemetry_summary_json(finished, tmp):
    assert json.load(open(os.path.join(tmp, "out", "telemetry_summary.json")))["id"] == finished["id"]


def test_finish_calls_file_for_the_run_date(finished, tmp):
    assert len(open(os.path.join(tmp, "telemetry", "calls-2026-11-03.jsonl")).readlines()) == 2


def test_finish_run_line_appended(finished, tmp):
    assert json.loads(open(os.path.join(tmp, "telemetry", "runs.jsonl")).readline())["cost_usd"] == finished["cost_usd"]


def test_summary_unknown_status_rejected():
    with pytest.raises(ValueError):
        tel().summary("finished-ish")


class Broken:
    def call(self, rec):
        raise OSError("disk full")

    def run(self, s, c):
        raise OSError("disk full")


@pytest.fixture(scope="module")
def broken_sink(tmp):
    t = tel(sinks=[Broken()], out_dir=os.path.join(tmp, "out2"))
    t.record("x", "big-model", U(1, 1))
    return t.finish("done")


def test_sinks_a_failing_sink_never_breaks_the_run(broken_sink):
    assert INCOMPLETE in broken_sink["flags"], broken_sink["flags"]


def test_sinks_failure_logged_for_errors_py(broken_sink, tmp):
    ev = [json.loads(line) for line in open(os.path.join(tmp, "out2", "errors", "telemetry.jsonl"))]
    assert ev and ev[0]["error_type"] == "telemetry_upload", ev


def test_report_per_step_table_and_run_line(finished, tmp):
    rep = T.report(os.path.join(tmp, "telemetry"), 7, today=dt.date(2026, 11, 3))
    assert "search" in rep and "judge" in rep and "2026-11-03" in rep and "total $" in rep, rep


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


KEY = "service-role-value-123456"
OWNER = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(scope="module")
def url():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pytest.MonkeyPatch.context() as mp:
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            mp.delenv(k, raising=False)
        mp.setenv("NO_PROXY", "127.0.0.1")
        yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def sb_run(url, tmp, plan, chunk=500, ncalls=3):
    Fake.log, Fake.plan = [], list(plan)
    sink = T.SupabaseSink(url, KEY, OWNER, chunk=chunk, sleep=lambda s: None)
    t = tel(sinks=[sink], out_dir=os.path.join(tmp, "out3"))
    for _ in range(ncalls):
        t.record("verify", "big-model", U(10, 10))
    return t.finish("done"), t, list(Fake.log)


@pytest.fixture(scope="module")
def sb_ok(url, tmp):
    return sb_run(url, tmp, [], chunk=2)


def test_supabase_run_first_then_calls_in_chunks_as_upserts(sb_ok):
    paths = [x["path"] for x in sb_ok[2]]
    assert paths == ["/rest/v1/telemetry_runs?on_conflict=id", "/rest/v1/telemetry_calls?on_conflict=id",
                     "/rest/v1/telemetry_calls?on_conflict=id"], paths


def test_supabase_service_key_sent_only_in_auth_headers(sb_ok):
    h = sb_ok[2][0]["headers"]
    assert h.get("apikey") == KEY and h.get("authorization") == f"Bearer {KEY}"


def test_supabase_merge_duplicates_so_retries_never_duplicate(sb_ok):
    assert "merge-duplicates" in sb_ok[2][0]["headers"].get("prefer", "")


def test_supabase_every_row_carries_the_owner_id(sb_ok):
    log = sb_ok[2]
    assert log[0]["body"][0]["user_id"] == OWNER and all(r["user_id"] == OWNER for x in log[1:] for r in x["body"])


def test_supabase_no_problems_on_success(sb_ok):
    assert sb_ok[1].sink_problems == [], sb_ok[1].sink_problems


def test_supabase_5xx_retried(url, tmp):
    _, t, log = sb_run(url, tmp, [503, 201])
    assert len([x for x in log if "runs" in x["path"]]) == 2 and t.sink_problems == [], t.sink_problems


@pytest.fixture(scope="module")
def sb_4xx(url, tmp):
    return sb_run(url, tmp, [400])


def test_supabase_4xx_not_retried_calls_not_sent_run_continues(sb_4xx):
    s, t, log = sb_4xx
    assert len(log) == 1 and t.sink_problems and INCOMPLETE in s["flags"], (len(log), t.sink_problems)


def test_supabase_key_never_in_problem_text(sb_4xx):
    assert all(KEY not in p for p in sb_4xx[1].sink_problems), sb_4xx[1].sink_problems


def test_supabase_unreachable_server_is_a_problem_not_an_exception(url):
    sink = T.SupabaseSink("http://127.0.0.1:9", KEY, OWNER, retries=2, timeout=1, sleep=lambda s: None)
    assert sink.run({"id": "x"}, []) != []


# ---- from_config: Supabase sink only when all three env vars are set
@pytest.fixture
def no_service_key(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)


def test_from_config_supabase_only_with_url_key_and_owner(no_service_key, url):
    a = T.Telemetry.from_config(env={})
    b = T.Telemetry.from_config(env={"SUPABASE_URL": url, "SUPABASE_SERVICE_ROLE_KEY": KEY, "SUPABASE_OWNER_ID": OWNER})
    assert [x.__class__.__name__ for x in a.sinks] == ["JsonlSink"]
    assert [x.__class__.__name__ for x in b.sinks] == ["JsonlSink", "SupabaseSink"]


def test_from_config_example_pricing_is_flagged_until_checked(no_service_key):
    a = T.Telemetry.from_config(env={})
    assert any("pricing_checked_on" in f for f in a.flags), a.flags
