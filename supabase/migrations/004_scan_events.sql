-- 004_scan_events.sql
-- Structured scan event log: every symbol scan writes events so the UI
-- can show the full decision path (started → analyzed → rejected/accepted → ordered/error).

create table if not exists scan_events (
  id               uuid        primary key default gen_random_uuid(),
  scan_id          text        not null,                  -- unique ID per run_scan() call (timestamp-based)
  symbol           text        not null,
  stage            text        not null,                  -- started | analyzed | rejected | accepted | ordered | error
  action           text,                                  -- hold | buy | sell | veto | risk_fail | spread_fail
  confidence       numeric(5,4),
  composite_score  numeric(8,4),
  setup_type       text,
  setup_quality    numeric(5,4),
  catalyst_score   numeric(5,3),
  rs_signal        text,                                  -- leader | laggard | neutral
  risk_status      text,                                  -- approved | rejected | vetoed
  rejection_reason text,
  payload          jsonb        default '{}'::jsonb,      -- full detail dump for drawer
  created_at       timestamptz  not null default now()
);

-- Index for dashboard queries
create index if not exists scan_events_scan_id_idx   on scan_events (scan_id);
create index if not exists scan_events_symbol_idx    on scan_events (symbol);
create index if not exists scan_events_created_idx   on scan_events (created_at desc);
create index if not exists scan_events_stage_idx     on scan_events (stage);

-- RLS: allow service role full access, anon read-only
alter table scan_events enable row level security;

create policy "service role all" on scan_events
  for all using (auth.role() = 'service_role');

create policy "anon read" on scan_events
  for select using (true);

-- Realtime: subscribe to new scan events for live dashboard feed.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
     and not exists (
       select 1
       from pg_publication_tables
       where pubname = 'supabase_realtime'
         and schemaname = 'public'
         and tablename = 'scan_events'
     ) then
    alter publication supabase_realtime add table scan_events;
  end if;
end $$;
