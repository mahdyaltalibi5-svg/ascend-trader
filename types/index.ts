// ── Trades ────────────────────────────────────────────────────────────────────

export type TradeStatus = "open" | "closed";

export interface Trade {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  entry_price: number | null;
  exit_price: number | null;
  status: TradeStatus;
  pnl: number | null;
  entry_at: string | null;
  exit_at: string | null;
  strategy: string;
  confidence_score: number | null;
  ai_reasoning: string | null;
  stop_loss: number | null;
  created_at: string;
}

// ── Signals ───────────────────────────────────────────────────────────────────

export interface Signal {
  id: string;
  symbol: string;
  signal: "buy" | "sell" | "hold";
  strategy: string;
  strength: number;
  confidence: number | null;
  criteria_met: number | null;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  risk_reward_ratio: number | null;
  ai_reasoning: string | null;
  market_regime: Record<string, number | string | boolean | null> | null;
  composite_score: number | null;
  executed: boolean;
  signal_outcomes?: SignalOutcome[];
  indicators: Record<string, unknown> | null;
  created_at: string;
}

export interface SignalOutcome {
  id: string;
  signal_id: string;
  symbol: string;
  signal: "buy" | "sell" | "hold";
  entry_price: number | null;
  price_1h: number | null;
  price_1d: number | null;
  price_3d: number | null;
  return_1h_pct: number | null;
  return_1d_pct: number | null;
  return_3d_pct: number | null;
  max_favorable_pct: number | null;
  max_adverse_pct: number | null;
  hit_stop: boolean | null;
  hit_take_profit: boolean | null;
  outcome_score: number | null;
  checked_at: string;
}

// ── Research Lab ─────────────────────────────────────────────────────────────

export interface BacktestRun {
  id: string;
  strategy: string;
  symbols: string[];
  timeframe: string;
  start_at: string;
  end_at: string;
  initial_equity: number;
  final_equity: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  win_rate: number;
  profit_factor: number;
  expectancy_r: number;
  sharpe: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  config: Record<string, unknown>;
  created_at: string;
}

export interface BacktestTrade {
  id: string;
  run_id: string;
  symbol: string;
  side: "buy" | "sell";
  entry_at: string;
  exit_at: string;
  entry_price: number;
  exit_price: number;
  stop_loss: number;
  take_profit: number;
  qty: number;
  pnl: number;
  r_multiple: number;
  confidence: number;
  criteria_met: number;
  exit_reason: string;
  indicators: Record<string, unknown>;
  created_at: string;
}

// ── Portfolio ─────────────────────────────────────────────────────────────────

export interface Position {
  symbol: string;
  qty: number;
  side: "buy" | "sell";
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
}

export interface Portfolio {
  id: string;
  equity: number;
  cash: number;
  buying_power: number;
  daily_pnl: number;
  total_pnl: number;
  updated_at: string;
  // virtual fields populated by hooks
  day_pnl: number;
  positions: Position[];
}

export interface PortfolioSnapshot {
  snapshot_at: string;
  equity: number;
  cash: number;
  daily_pnl: number;
  total_pnl: number;
}

// ── Bot ───────────────────────────────────────────────────────────────────────

export interface BotStatus {
  running: boolean;
  strategy: string;
  last_signal_at: string | null;
  trades_today: number;
  win_rate: number;
  uptime_seconds: number;
}

export interface BotLog {
  id: string;
  message: string;
  level: "info" | "warning" | "error";
  created_at: string;
}

// ── Charts ────────────────────────────────────────────────────────────────────

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
