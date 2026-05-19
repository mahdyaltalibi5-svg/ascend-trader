-- =============================================================================
-- Ascend Trader — Initial Schema
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists portfolio (
  id           uuid primary key default gen_random_uuid(),
  equity       numeric          not null default 0,
  cash         numeric          not null default 0,
  buying_power numeric          not null default 0,
  daily_pnl    numeric          not null default 0,
  total_pnl    numeric          not null default 0,
  updated_at   timestamptz      not null default now()
);

create table if not exists trades (
  id               uuid        primary key default gen_random_uuid(),
  symbol           text        not null,
  side             text        not null check (side in ('buy', 'sell')),
  qty              numeric     not null,
  entry_price      numeric     not null,
  exit_price       numeric,
  status           text        not null default 'open' check (status in ('open', 'closed')),
  pnl              numeric,
  entry_at         timestamptz not null,
  exit_at          timestamptz,
  strategy         text        not null,
  confidence_score numeric     not null default 0 check (confidence_score between 0 and 1),
  ai_reasoning     text,
  created_at       timestamptz not null default now()
);

create table if not exists signals (
  id         uuid        primary key default gen_random_uuid(),
  symbol     text        not null,
  signal     text        not null check (signal in ('buy', 'sell', 'hold')),
  strategy   text        not null,
  strength   numeric     not null default 0 check (strength between 0 and 1),
  indicators jsonb       not null default '{}',
  created_at timestamptz not null default now()
);

create table if not exists bot_logs (
  id         uuid        primary key default gen_random_uuid(),
  message    text        not null,
  level      text        not null default 'info' check (level in ('info', 'warning', 'error')),
  created_at timestamptz not null default now()
);

create table if not exists portfolio_snapshots (
  id         uuid        primary key default gen_random_uuid(),
  equity     numeric     not null default 0,
  cash       numeric     not null default 0,
  daily_pnl  numeric     not null default 0,
  total_pnl  numeric     not null default 0,
  snapshot_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

create index if not exists trades_status_idx    on trades  (status);
create index if not exists trades_symbol_idx    on trades  (symbol);
create index if not exists signals_symbol_idx   on signals (symbol);
create index if not exists signals_created_idx  on signals (created_at desc);
create index if not exists bot_logs_created_idx on bot_logs (created_at desc);

-- ---------------------------------------------------------------------------
-- Trigger: snapshot portfolio on every update
-- ---------------------------------------------------------------------------

create or replace function snapshot_portfolio()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into portfolio_snapshots (equity, cash, daily_pnl, total_pnl, snapshot_at)
  values (new.equity, new.cash, new.daily_pnl, new.total_pnl, now());
  return new;
end;
$$;

drop trigger if exists trg_snapshot_portfolio on portfolio;

create trigger trg_snapshot_portfolio
  after update on portfolio
  for each row
  execute function snapshot_portfolio();

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------

alter table portfolio           enable row level security;
alter table trades              enable row level security;
alter table signals             enable row level security;
alter table bot_logs            enable row level security;
alter table portfolio_snapshots enable row level security;

-- Permissive policies for authenticated users (tighten per-role in a later migration)

create policy "authenticated_all" on portfolio
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on trades
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on signals
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on bot_logs
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on portfolio_snapshots
  for all to authenticated using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Realtime
-- ---------------------------------------------------------------------------

-- Realtime is enabled per-publication. Add tables to the supabase_realtime
-- publication so clients can subscribe to changes.

do $$
begin
  -- portfolio
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'portfolio'
  ) then
    alter publication supabase_realtime add table portfolio;
  end if;

  -- trades
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'trades'
  ) then
    alter publication supabase_realtime add table trades;
  end if;

  -- signals
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'signals'
  ) then
    alter publication supabase_realtime add table signals;
  end if;

  -- portfolio_snapshots
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'portfolio_snapshots'
  ) then
    alter publication supabase_realtime add table portfolio_snapshots;
  end if;
end;
$$;
