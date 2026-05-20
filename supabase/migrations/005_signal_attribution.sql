-- 005_signal_attribution.sql
-- Per-component signal accuracy tracking.
-- Written by evaluate_signal_outcomes() after each trade outcome is graded.
-- Aggregated by signal_attribution.py to tell the bot which signals are
-- actually predictive vs. noise.

create table if not exists signal_attribution (
  id                       uuid        primary key default gen_random_uuid(),
  signal_id                uuid        references signals(id) on delete cascade,
  symbol                   text        not null,
  setup_type               text,
  regime                   text,
  signal_side              text,                          -- buy | sell

  -- Confidence at each stage
  confidence_raw           numeric(5,4),                 -- before all boosts
  confidence_final         numeric(5,4),                 -- after full pipeline

  -- Component boost values at time of signal
  catalyst_score           numeric(5,4),
  rs_boost                 numeric(5,4),
  insider_boost            numeric(5,4),
  options_flow_boost       numeric(5,4),
  short_interest_boost     numeric(5,4),
  memory_boost             numeric(5,4),

  -- Outcome
  outcome_score            numeric(6,3),
  r_multiple               numeric(6,3),
  won                      boolean      not null default false,
  hit_stop                 boolean      not null default false,
  hit_target               boolean      not null default false,

  -- Attribution flags: did each component push in the correct direction?
  rs_contributed           boolean,
  insider_contributed      boolean,
  options_contributed      boolean,
  short_interest_contributed boolean,
  catalyst_contributed     boolean,

  created_at               timestamptz  not null default now()
);

-- Indexes for aggregation queries
create index if not exists signal_attribution_symbol_idx   on signal_attribution (symbol);
create index if not exists signal_attribution_regime_idx   on signal_attribution (regime);
create index if not exists signal_attribution_setup_idx    on signal_attribution (setup_type);
create index if not exists signal_attribution_created_idx  on signal_attribution (created_at desc);
create index if not exists signal_attribution_won_idx      on signal_attribution (won);

-- One attribution row per signal (upsert key)
create unique index if not exists signal_attribution_signal_id_idx
  on signal_attribution (signal_id);

-- RLS
alter table signal_attribution enable row level security;

create policy "service role all" on signal_attribution
  for all using (auth.role() = 'service_role');

create policy "anon read" on signal_attribution
  for select using (true);
