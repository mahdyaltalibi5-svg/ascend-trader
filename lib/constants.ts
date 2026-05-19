export const ALPACA_BASE_URL =
  process.env.ALPACA_BASE_URL ?? "https://paper-api.alpaca.markets";

export const BOT_API_URL =
  process.env.NEXT_PUBLIC_BOT_API_URL ?? "http://localhost:8000";

export const SUPPORTED_SYMBOLS = [
  "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
  "GOOGL", "META", "SPY", "QQQ", "SOFI",
] as const;

export const STRATEGIES = [
  { id: "momentum", label: "Momentum" },
  { id: "mean_reversion", label: "Mean Reversion" },
  { id: "breakout", label: "Breakout" },
  { id: "ai_signal", label: "AI Signal (Claude)" },
] as const;

export const TIMEFRAMES = [
  { value: "1Min", label: "1m" },
  { value: "5Min", label: "5m" },
  { value: "15Min", label: "15m" },
  { value: "1Hour", label: "1h" },
  { value: "1Day", label: "1D" },
] as const;

export const MAX_POSITION_SIZE_PCT = 0.1; // 10% of portfolio per position
export const DEFAULT_STOP_LOSS_PCT = 0.02; // 2%
export const DEFAULT_TAKE_PROFIT_PCT = 0.04; // 4%
