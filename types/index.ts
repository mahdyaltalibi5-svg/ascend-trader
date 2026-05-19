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
  indicators: Record<string, number | string> | null;
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
