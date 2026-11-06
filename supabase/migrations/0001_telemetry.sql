-- Telemetry for the API version: one row per run, one row per model call. No prompt or response text, no candidate
-- data: counts, model ids, step names, timings, error kinds and estimated cost only (see scripts/telemetry.py).
--
-- Writes: only the scheduled job, with the service-role key (it bypasses row-level security). There are deliberately
-- NO insert/update/delete policies, so the browser (anon key) and signed-in users cannot write or alter telemetry.
-- Reads: a signed-in user sees only their own rows.

create table if not exists public.telemetry_runs (
  id                    uuid primary key,
  user_id               uuid not null references auth.users (id) on delete cascade,
  run_date              date not null,
  started_at            timestamptz not null,
  finished_at           timestamptz,
  duration_s            numeric(10, 1),
  status                text not null check (status in ('done', 'done_no_email', 'stopped', 'failed', 'budget_reached', 'dry_run')),
  mode                  text,
  email_mode            text check (email_mode is null or email_mode in ('full', 'short', 'none')),
  engine_version        text,
  calls                 integer not null default 0 check (calls >= 0),
  input_tokens          bigint  not null default 0 check (input_tokens >= 0),
  output_tokens         bigint  not null default 0 check (output_tokens >= 0),
  cache_write_5m_tokens bigint  not null default 0 check (cache_write_5m_tokens >= 0),
  cache_write_1h_tokens bigint  not null default 0 check (cache_write_1h_tokens >= 0),
  cache_read_tokens     bigint  not null default 0 check (cache_read_tokens >= 0),
  web_search_requests   integer not null default 0 check (web_search_requests >= 0),
  web_fetch_requests    integer not null default 0 check (web_fetch_requests >= 0),
  cache_hit_rate        numeric(4, 3),
  cost_usd              numeric(12, 4) not null default 0 check (cost_usd >= 0),
  cost_complete         boolean not null default true,
  unpriced_models       text[] not null default '{}',
  budget_usd            numeric(10, 2),
  cost_per_pick_usd     numeric(12, 4),
  counts                jsonb not null default '{}'::jsonb,
  step_seconds          jsonb not null default '{}'::jsonb,
  by_step               jsonb not null default '{}'::jsonb,
  by_model              jsonb not null default '{}'::jsonb,
  error_counts          jsonb not null default '{}'::jsonb,
  flags                 text[] not null default '{}',
  schema_version        smallint not null default 1,
  inserted_at           timestamptz not null default now()
);

create table if not exists public.telemetry_calls (
  id                    text primary key,                -- "<run id>:<seq>", so a retried upload never duplicates
  run_id                uuid not null references public.telemetry_runs (id) on delete cascade,
  user_id               uuid not null references auth.users (id) on delete cascade,
  seq                   integer not null check (seq > 0),
  run_date              date not null,
  at                    timestamptz not null,
  step                  text not null check (char_length(step) <= 40),
  model                 text not null default '' check (char_length(model) <= 80),
  input_tokens          integer not null default 0 check (input_tokens >= 0),
  output_tokens         integer not null default 0 check (output_tokens >= 0),
  cache_write_5m_tokens integer not null default 0 check (cache_write_5m_tokens >= 0),
  cache_write_1h_tokens integer not null default 0 check (cache_write_1h_tokens >= 0),
  cache_read_tokens     integer not null default 0 check (cache_read_tokens >= 0),
  web_search_requests   integer not null default 0 check (web_search_requests >= 0),
  web_fetch_requests    integer not null default 0 check (web_fetch_requests >= 0),
  latency_ms            integer not null default 0 check (latency_ms >= 0),
  stop_reason           text,
  ok                    boolean not null,
  error_kind            text check (error_kind is null or error_kind in
                          ('rate_limited', 'overloaded', 'timeout', 'auth', 'connection', 'bad_request', 'server_error', 'other')),
  error                 text check (error is null or char_length(error) <= 200),
  request_id            text,
  batch                 boolean not null default false,
  cost_usd              numeric(12, 6),
  schema_version        smallint not null default 1,
  unique (run_id, seq)
);

create index if not exists telemetry_runs_user_date on public.telemetry_runs (user_id, run_date desc);
create index if not exists telemetry_calls_run on public.telemetry_calls (run_id);
create index if not exists telemetry_calls_user_date_step on public.telemetry_calls (user_id, run_date, step);

alter table public.telemetry_runs  enable row level security;
alter table public.telemetry_calls enable row level security;
alter table public.telemetry_runs  force row level security;
alter table public.telemetry_calls force row level security;

-- Read-only access to your own rows. (Re-runnable: drop then create.)
drop policy if exists telemetry_runs_read_own on public.telemetry_runs;
create policy telemetry_runs_read_own on public.telemetry_runs
  for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists telemetry_calls_read_own on public.telemetry_calls;
create policy telemetry_calls_read_own on public.telemetry_calls
  for select to authenticated using (user_id = (select auth.uid()));

-- Belt and braces: the browser roles get SELECT only (RLS still limits it to the owner's rows); anon gets nothing.
revoke all on public.telemetry_runs, public.telemetry_calls from anon, authenticated;
grant select on public.telemetry_runs, public.telemetry_calls to authenticated;

-- Daily view for the tracker page's run strip. security_invoker makes it obey the caller's RLS.
create or replace view public.telemetry_daily with (security_invoker = true) as
select user_id,
       run_date,
       count(*)                                         as runs,
       sum(calls)                                       as calls,
       sum(input_tokens + cache_write_5m_tokens + cache_write_1h_tokens + cache_read_tokens) as input_tokens_all,
       sum(output_tokens)                               as output_tokens,
       sum(web_search_requests)                         as web_searches,
       round(sum(cost_usd), 2)                          as cost_usd,
       bool_and(cost_complete)                          as cost_complete,
       sum(coalesce((counts ->> 'picks')::int, 0))      as picks,
       case when sum(coalesce((counts ->> 'picks')::int, 0)) > 0
            then round(sum(cost_usd) / sum(coalesce((counts ->> 'picks')::int, 0)), 2) end as cost_per_pick_usd
from public.telemetry_runs
group by user_id, run_date;

revoke all on public.telemetry_daily from anon, authenticated;
grant select on public.telemetry_daily to authenticated;
