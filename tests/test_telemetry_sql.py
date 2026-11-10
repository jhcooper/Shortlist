"""Applies supabase/migrations/0001_telemetry.sql to a throwaway local Postgres with a minimal stand-in for Supabase's
auth schema and roles, then checks: the migration re-runs cleanly, the job's rows (made by scripts/telemetry.py) insert
with exactly the table's columns, upserts don't duplicate, and row-level security lets a signed-in user read only their
own rows while the browser roles can't write anything. Skips if Postgres isn't installed."""
import glob
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import telemetry as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINS = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb")) or [shutil.which("initdb")]
pytestmark = pytest.mark.skipif(not BINS or not BINS[-1], reason="Postgres not installed")
OWNER, OTHER = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
STAND_IN = """
  create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
  create schema auth; create table auth.users (id uuid primary key);
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema auth, public to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
  insert into auth.users values ('11111111-1111-4111-8111-111111111111'), ('22222222-2222-4222-8222-222222222222');
"""


def upsert(table, rows):
    cols = list(rows[0].keys())
    j = json.dumps(rows).replace("'", "''")
    upd = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    return (f"insert into public.{table} ({', '.join(cols)}) select {', '.join(cols)} from "
            f"jsonb_populate_recordset(null::public.{table}, '{j}'::jsonb) on conflict (id) do update set {upd};")


@pytest.fixture(scope="module")
def db():
    """A throwaway cluster on a private socket, with the stand-in, the migration applied twice, and the job's rows
    uploaded twice. Returns psql() plus the result of each setup step."""
    bindir = os.path.dirname(BINS[-1])
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    d = tempfile.mkdtemp(prefix="pgtel")
    os.chmod(d, 0o777)
    data, sock, port = os.path.join(d, "data"), d, "54329"
    if as_pg:
        subprocess.run(["chown", "postgres", d], check=True)
    subprocess.run(as_pg + [f"{bindir}/initdb", "-D", data, "-A", "trust", "-U", "postgres"], check=True, capture_output=True)
    subprocess.run(as_pg + [f"{bindir}/pg_ctl", "-D", data, "-w", "-l", os.path.join(d, "log"), "-o",
                            f"-k {sock} -p {port} -c listen_addresses=''", "start"], check=True, capture_output=True)

    def psql(sql, role=None, sub=None):
        pre = ""
        if sub:
            pre += f"set request.jwt.claim.sub = '{sub}';\n"
        if role:
            pre += f"set role {role};\n"
        r = subprocess.run(as_pg + [f"{bindir}/psql", "-h", sock, "-p", port, "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                                    "-qAt", "-f", "-"], input=pre + sql, capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    try:
        steps = {"stand_in": psql(STAND_IN)}
        mig = open(os.path.join(ROOT, "supabase", "migrations", "0001_telemetry.sql"), encoding="utf-8").read()
        steps["migrate"] = psql(mig)
        steps["migrate_again"] = psql(mig)
        # rows exactly as the job would send them
        pricing = T.Pricing.from_dict({"pricing": {"m": {"input": 1.0, "output": 5.0}}, "web_search_usd_per_1k": 10})
        ms = T.MemorySink()
        t = T.Telemetry("2026-11-03", sinks=[ms], pricing=pricing, budget_usd=2.0)
        t.record("search", "m", {"input_tokens": 100, "output_tokens": 20, "server_tool_use": {"web_search_requests": 1}}, 900)
        t.record("judge", "m", None, 30, ok=False, error_kind="overloaded", error="Overloaded")
        t.count("picks", 1)
        run = dict(t.finish("done", email_mode="full"), user_id=OWNER)
        calls = [dict(c, user_id=OWNER) for c in ms.calls]
        steps["upload"] = psql(upsert("telemetry_runs", [run]) + upsert("telemetry_calls", calls), role="service_role")
        steps["upload_again"] = psql(upsert("telemetry_runs", [run]) + upsert("telemetry_calls", calls), role="service_role")
        yield {"psql": psql, "steps": steps, "run": run, "calls": calls}
    finally:
        subprocess.run(as_pg + [f"{bindir}/pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("step", ["stand_in", "migrate", "migrate_again", "upload", "upload_again"],
                         ids=["stand-in auth schema", "migration applies", "migration re-runs cleanly",
                              "job (service role) inserts run + calls; Python keys match columns", "retried upload is an upsert"])
def test_setup_step(db, step):
    rc, _, err = db["steps"][step]
    assert rc == 0, err


def test_no_duplicates_after_retry(db):
    _, out, _ = db["psql"]("select (select count(*) from public.telemetry_runs) || ',' || (select count(*) from public.telemetry_calls);")
    assert out == "1,2", out


def test_owner_reads_own_run_calls_and_daily_view(db):
    rc, out, err = db["psql"]("select count(*) from public.telemetry_runs; select count(*) from public.telemetry_calls;"
                              "select runs, picks, cost_usd from public.telemetry_daily;", role="authenticated", sub=OWNER)
    assert rc == 0 and out.split("\n") == ["1", "2", "1|1|0.01"], out or err


def test_another_user_sees_nothing(db):
    rc, out, err = db["psql"]("select count(*) from public.telemetry_runs; select count(*) from public.telemetry_calls;"
                              "select count(*) from public.telemetry_daily;", role="authenticated", sub=OTHER)
    assert rc == 0 and out.split("\n") == ["0", "0", "0"], out or err


DENIED = [("anon", "select count(*) from public.telemetry_runs;"),
          ("anon", "select count(*) from public.telemetry_daily;"),
          ("authenticated", f"insert into public.telemetry_runs (id, user_id, run_date, started_at, status) values "
                            f"(gen_random_uuid(), '{OWNER}', '2026-11-03', now(), 'done');"),
          ("authenticated", "update public.telemetry_runs set cost_usd = 0;"),
          ("authenticated", "delete from public.telemetry_calls;")]


@pytest.mark.parametrize(("role", "sql"), DENIED,
                         ids=[f"{r} denied: {s.split()[0]} {s.split()[2] if s.startswith('select') else ''}".strip() for r, s in DENIED])
def test_browser_roles_denied(db, role, sql):
    rc, out, err = db["psql"](sql, role=role, sub=OWNER)
    assert rc != 0 and "permission denied" in err, err or out


def test_unknown_status_rejected_by_the_table(db):
    bad = dict(db["run"], id="00000000-0000-4000-8000-0000000000aa", status="finished-ish")
    rc, _, err = db["psql"](upsert("telemetry_runs", [bad]), role="service_role")
    assert rc != 0 and "check constraint" in err, err


def test_over_long_error_text_rejected_by_the_table(db):
    longc = dict(db["calls"][0], id="x:9999", seq=9999, error="e" * 300)
    rc, _, err = db["psql"](upsert("telemetry_calls", [longc]), role="service_role")
    assert rc != 0 and "check constraint" in err, err


def test_row_level_security_on_for_every_telemetry_table(db):
    _, out, _ = db["psql"]("select count(*) from pg_class where relname like 'telemetry_%' and relkind = 'r' and not relrowsecurity;")
    assert out == "0", out
