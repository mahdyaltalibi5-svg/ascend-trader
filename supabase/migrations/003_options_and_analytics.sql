-- =============================================================================
-- Ascend Trader — Options & Analytics Schema
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Alter existing tables (idempotent column additions)
-- ---------------------------------------------------------------------------

alter table signals add column if not exists earnings_catalyst boolean default false;
alter table trades  add column if not exists options_mode       boolean default false;
alter table trades  add column if not exists option_contract    text;
alter table trades  add column if not exists option_premium     numeric;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists options_trades (
  id               uuid        primary key default gen_random_uuid(),
  trade_id         uuid        references trades(id),
  underlying       text        not null,
  contract_symbol  text        not null,
  option_type      text        not null check (option_type in ('call', 'put')),
  strike           numeric     not null,
  expiry           date        not null,
  entry_premium    numeric     not null,
  exit_premium     numeric,
  qty              int         not null,
  pnl              numeric,
  status           text        not null default 'open',
  created_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

create index if not exists options_trades_underlying_idx on options_trades (underlying);
create index if not exists options_trades_status_idx     on options_trades (status);
create index if not exists signals_earnings_catalyst_idx on signals (earnings_catalyst) where earnings_catalyst = true;

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------

alter table options_trades enable row level security;

create policy "authenticated_all" on options_trades
  for all to authenticated using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Realtime
-- ---------------------------------------------------------------------------

do $$
begin
  -- options_trades
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'options_trades'
  ) then
    alter publication supabase_realtime add table options_trades;
  end if;
end;
$$;
