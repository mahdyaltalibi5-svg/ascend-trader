-- =============================================================================
-- Ascend Trader - Research Lab + Signal Memory
-- =============================================================================

-- The live bot already writes stop_loss; keep the database aligned with runtime.
alter table trades
  add column if not exists stop_loss numeric;

-- Richer signal memory. These fields let the bot audit every idea, not only trades.
alter table signals
  add column if not exists confidence numeric check (confidence is null or confidence between 0 and 1),
  add column if not exists criteria_met integer check (criteria_met is null or criteria_met between 0 and 7),
  add column if not exists entry_price numeric,
  add column if not exists stop_loss numeric,
  add column if not exists take_profit numeric,
  add column if not exists risk_reward_ratio numeric,
  add column if not exists ai_reasoning text,
  add column if not exists market_regime jsonb not null default '{}',
  add column if not exists composite_score numeric not null default 0,
  add column if not exists executed boolean not null default false,
  add column if not exists outcome_checked_at timestamptz;

create table if not exists signal_outcomes (
  id                  uuid        primary key default gen_random_uuid(),
  signal_id           uuid        not null references signals(id) on delete cascade,
  symbol              text        not null,
  signal              text        not null check (signal in ('buy', 'sell', 'hold')),
  entry_price         numeric,
  price_1h            numeric,
  price_1d            numeric,
  price_3d            numeric,
  return_1h_pct       numeric,
  return_1d_pct       numeric,
  return_3d_pct       numeric,
  max_favorable_pct   numeric,
  max_adverse_pct     numeric,
  hit_stop            boolean,
  hit_take_profit     boolean,
  outcome_score       numeric,
  checked_at          timestamptz not null default now(),
  unique (signal_id)
);

create table if not exists backtest_runs (
  id                uuid        primary key default gen_random_uuid(),
  strategy          text        not null,
  symbols           text[]      not null,
  timeframe         text        not null,
  start_at          timestamptz not null,
  end_at            timestamptz not null,
  initial_equity    numeric     not null,
  final_equity      numeric     not null,
  total_return_pct  numeric     not null,
  max_drawdown_pct  numeric     not null,
  win_rate          numeric     not null,
  profit_factor     numeric     not null,
  expectancy_r      numeric     not null,
  sharpe            numeric     not null,
  total_trades      integer     not null,
  winning_trades    integer     not null,
  losing_trades     integer     not null,
  config            jsonb       not null default '{}',
  created_at        timestamptz not null default now()
);

create table if not exists backtest_trades (
  id                uuid        primary key default gen_random_uuid(),
  run_id            uuid        not null references backtest_runs(id) on delete cascade,
  symbol            text        not null,
  side              text        not null check (side in ('buy', 'sell')),
  entry_at          timestamptz not null,
  exit_at           timestamptz not null,
  entry_price       numeric     not null,
  exit_price        numeric     not null,
  stop_loss         numeric     not null,
  take_profit       numeric     not null,
  qty               numeric     not null,
  pnl               numeric     not null,
  r_multiple        numeric     not null,
  confidence        numeric     not null,
  criteria_met      integer     not null,
  exit_reason       text        not null,
  indicators        jsonb       not null default '{}',
  created_at        timestamptz not null default now()
);

create index if not exists signal_outcomes_symbol_idx on signal_outcomes (symbol);
create index if not exists signal_outcomes_checked_idx on signal_outcomes (checked_at desc);
create index if not exists backtest_runs_created_idx on backtest_runs (created_at desc);
create index if not exists backtest_trades_run_idx on backtest_trades (run_id);
create index if not exists backtest_trades_symbol_idx on backtest_trades (symbol);

alter table signal_outcomes enable row level security;
alter table backtest_runs enable row level security;
alter table backtest_trades enable row level security;

create policy "authenticated_all" on signal_outcomes
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on backtest_runs
  for all to authenticated using (true) with check (true);

create policy "authenticated_all" on backtest_trades
  for all to authenticated using (true) with check (true);

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'signal_outcomes'
  ) then
    alter publication supabase_realtime add table signal_outcomes;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'backtest_runs'
  ) then
    alter publication supabase_realtime add table backtest_runs;
  end if;
end;
$$;
