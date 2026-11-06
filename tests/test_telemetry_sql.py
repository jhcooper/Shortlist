"""Applies supabase/migrations/0001_telemetry.sql to a throwaway local Postgres with a minimal stand-in for Supabase's
auth schema and roles, then checks: the migration re-runs cleanly, the job's rows (made by scripts/telemetry.py) insert
with exactly the table's columns, upserts don't duplicate, and row-level security lets a signed-in user read only their
own rows while the browser roles can't write anything.
Run: python3 tests/test_telemetry_sql.py   (skips with exit 0 if Postgres isn't installed)."""
import os, sys, glob, json, shutil, subprocess, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import telemetry as T  # noqa: E402

bins = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb")) or [shutil.which("initdb")]
if not bins or not bins[-1]:
    print("Postgres not installed: SQL checks skipped")
    sys.exit(0)
BIN = os.path.dirname(bins[-1])
as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
fails, n = [], 0


def check(name, ok, info=""):
    global n
    n += 1
    if not ok:
        fails.append(f"{name}{': ' + str(info)[:300] if info else ''}")


d = tempfile.mkdtemp(prefix="pgtel")
os.chmod(d, 0o777)
data, sock, port = os.path.join(d, "data"), d, "54329"
if as_pg:
    subprocess.run(["chown", "postgres", d], check=True)
subprocess.run(as_pg + [f"{BIN}/initdb", "-D", data, "-A", "trust", "-U", "postgres"], check=True, capture_output=True)
subprocess.run(as_pg + [f"{BIN}/pg_ctl", "-D", data, "-w", "-l", os.path.join(d, "log"), "-o",
                        f"-k {sock} -p {port} -c listen_addresses=''", "start"], check=True, capture_output=True)


def psql(sql, role=None, sub=None):
    pre = ""
    if sub:
        pre += f"set request.jwt.claim.sub = '{sub}';\n"
    if role:
        pre += f"set role {role};\n"
    r = subprocess.run(as_pg + [f"{BIN}/psql", "-h", sock, "-p", port, "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                                "-qAt", "-f", "-"], input=pre + sql, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


try:
    # minimal Supabase stand-in: roles, auth.users, auth.uid(), and Supabase's permissive default grants
    rc, out, err = psql("""
      create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
      create schema auth; create table auth.users (id uuid primary key);
      create function auth.uid() returns uuid language sql stable as
        $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
      grant usage on schema auth, public to anon, authenticated, service_role;
      grant execute on function auth.uid() to anon, authenticated, service_role;
      alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
      alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
      insert into auth.users values ('11111111-1111-4111-8111-111111111111'), ('22222222-2222-4222-8222-222222222222');
    """)
    check("stand-in auth schema", rc == 0, err)
    mig = open(os.path.join(ROOT, "supabase", "migrations", "0001_telemetry.sql"), encoding="utf-8").read()
    rc, out, err = psql(mig)
    check("migration applies", rc == 0, err)
    rc, out, err = psql(mig)
    check("migration re-runs cleanly", rc == 0, err)

    OWNER, OTHER = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    # rows exactly as the job would send them
    P = T.Pricing.from_dict({"pricing": {"m": {"input": 1.0, "output": 5.0}}, "web_search_usd_per_1k": 10})
    ms = T.MemorySink()
    t = T.Telemetry("2026-11-03", sinks=[ms], pricing=P, budget_usd=2.0)
    t.record("search", "m", {"input_tokens": 100, "output_tokens": 20, "server_tool_use": {"web_search_requests": 1}}, 900)
    t.record("judge", "m", None, 30, ok=False, error_kind="overloaded", error="Overloaded")
    t.count("picks", 1)
    run = dict(t.finish("done", email_mode="full"), user_id=OWNER)
    calls = [dict(c, user_id=OWNER) for c in ms.calls]

    def upsert(table, rows):
        cols = list(rows[0].keys())
        j = json.dumps(rows).replace("'", "''")
        upd = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
        return (f"insert into public.{table} ({', '.join(cols)}) select {', '.join(cols)} from "
                f"jsonb_populate_recordset(null::public.{table}, '{j}'::jsonb) on conflict (id) do update set {upd};")

    rc, out, err = psql(upsert("telemetry_runs", [run]) + upsert("telemetry_calls", calls), role="service_role")
    check("job (service role) inserts run + calls; Python keys match columns", rc == 0, err)
    rc, out, err = psql(upsert("telemetry_runs", [run]) + upsert("telemetry_calls", calls), role="service_role")
    check("retried upload is an upsert", rc == 0, err)
    rc, out, err = psql("select (select count(*) from public.telemetry_runs) || ',' || (select count(*) from public.telemetry_calls);")
    check("no duplicates after retry", out == "1,2", out)

    rc, out, err = psql("select count(*) from public.telemetry_runs; select count(*) from public.telemetry_calls;"
                        "select runs, picks, cost_usd from public.telemetry_daily;", role="authenticated", sub=OWNER)
    check("owner reads own run, calls and daily view", rc == 0 and out.split("\n") == ["1", "2", "1|1|0.01"], out or err)
    rc, out, err = psql("select count(*) from public.telemetry_runs; select count(*) from public.telemetry_calls;"
                        "select count(*) from public.telemetry_daily;", role="authenticated", sub=OTHER)
    check("another user sees nothing (tables and view)", rc == 0 and out.split("\n") == ["0", "0", "0"], out or err)
    for role, sql in (("anon", "select count(*) from public.telemetry_runs;"),
                      ("anon", "select count(*) from public.telemetry_daily;"),
                      ("authenticated", f"insert into public.telemetry_runs (id, user_id, run_date, started_at, status) values "
                                        f"(gen_random_uuid(), '{OWNER}', '2026-11-03', now(), 'done');"),
                      ("authenticated", "update public.telemetry_runs set cost_usd = 0;"),
                      ("authenticated", "delete from public.telemetry_calls;")):
        rc, out, err = psql(sql, role=role, sub=OWNER)
        check(f"{role} denied: {sql.split()[0]} {sql.split()[2] if sql.startswith('select') else ''}".strip(),
              rc != 0 and "permission denied" in err, err or out)
    bad = dict(run, id="00000000-0000-4000-8000-0000000000aa", status="finished-ish")
    rc, out, err = psql(upsert("telemetry_runs", [bad]), role="service_role")
    check("unknown status rejected by the table", rc != 0 and "check constraint" in err, err)
    longc = dict(calls[0], id="x:9999", seq=9999, error="e" * 300)
    rc, out, err = psql(upsert("telemetry_calls", [longc]), role="service_role")
    check("over-long error text rejected by the table", rc != 0 and "check constraint" in err, err)
    rc, out, err = psql("select count(*) from pg_class where relname like 'telemetry_%' and relkind = 'r' and not relrowsecurity;")
    check("row-level security on for every telemetry table", out == "0", out)
finally:
    subprocess.run(as_pg + [f"{BIN}/pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(d, ignore_errors=True)

print(f"{n - len(fails)}/{n} passed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
