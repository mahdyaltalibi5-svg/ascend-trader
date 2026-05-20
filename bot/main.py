"""
Ascend Trader Elite v3 — Maximum performance AI trading bot.
Multi-timeframe analysis + live position management + trailing stops +
circuit breaker + news sentiment + composite signal scoring.
"""

import os
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import anthropic
from dotenv import load_dotenv
from backtest import fetch_historical_bars, parse_config, run_research_backtest

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Expanded watchlist — mega caps + high-beta + crypto-adjacent + volatile growth
WATCHLIST = [
    # Mega cap (high liquidity, big moves on catalysts)
    "NVDA", "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL",
    # High-beta tech (2-5% daily moves common)
    "AMD", "PLTR", "SMCI", "ARM", "CRWD", "PANW", "SOFI",
    # Crypto-adjacent (follow BTC, very volatile)
    "COIN", "HOOD", "MSTR", "MARA", "RIOT",
    # Volatile growth
    "IONQ", "RKLB", "RXRX", "ACHR",
]

# Regime detection only — not traded
REGIME_SYMBOLS = ["SPY", "QQQ"]

MAX_POSITIONS       = 6       # max simultaneous open trades
RISK_PER_TRADE      = 0.02    # risk 2% of portfolio per trade
MIN_CONFIDENCE      = 0.70    # minimum AI confidence to execute
MIN_RR              = 2.0     # minimum risk/reward ratio accepted
MAX_DAILY_LOSS_PCT  = 0.04    # circuit breaker: stop at -4% daily
SCAN_INTERVAL_SECS  = 300     # full market scan every 5 minutes
MONITOR_INTERVAL    = 90      # position check every 90 seconds
BATCH_SIZE          = 5       # symbols per concurrent batch

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
supabase: Client = create_client(
    os.environ["NEXT_PUBLIC_SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ALPACA_TRADE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL  = "https://data.alpaca.markets"
ALPACA_HEADERS   = {
    "APCA-API-KEY-ID":     os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
bot_state = {
    "running":                 False,
    "circuit_breaker_active":  False,
    "strategy":                "ascend_elite_v3",
    "last_scan_at":            None,
    "last_signal_at":          None,
    "last_monitor_at":         None,
    "last_outcome_eval_at":    None,
    "trades_today":            0,
    "signals_today":           0,
    "wins_today":              0,
    "losses_today":            0,
    "win_rate":                0.0,
    "day_pnl":                 0.0,
    "started_at":              None,
    "scan_count":              0,
}

scan_task:    Optional[asyncio.Task] = None
monitor_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------
async def alpaca_get(path: str, base: str = ALPACA_TRADE_URL) -> dict | list:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{base}{path}", headers=ALPACA_HEADERS)
        r.raise_for_status()
        return r.json()


async def alpaca_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{ALPACA_TRADE_URL}{path}", json=payload, headers=ALPACA_HEADERS
        )
        r.raise_for_status()
        return r.json()


async def alpaca_delete(path: str) -> bool:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.delete(f"{ALPACA_TRADE_URL}{path}", headers=ALPACA_HEADERS)
        return r.status_code in (200, 204)

# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
async def fetch_bars(symbol: str, timeframe: str, limit: int = 60) -> pd.DataFrame:
    try:
        data = await alpaca_get(
            f"/v2/stocks/{symbol}/bars?timeframe={timeframe}&limit={limit}&adjustment=raw",
            base=ALPACA_DATA_URL,
        )
        bars = data.get("bars", [])
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return df.set_index("t")
    except Exception:
        return pd.DataFrame()


async def fetch_news(symbol: str) -> list[dict]:
    try:
        data = await alpaca_get(
            f"/v1beta1/news?symbols={symbol}&limit=8",
            base=ALPACA_DATA_URL,
        )
        return data.get("news", [])
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 22:
        return {}
    try:
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        rsi_s     = ta.rsi(close, length=14)
        rsi       = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.isna().all() else None

        macd_df   = ta.macd(close, fast=12, slow=26, signal=9)
        macd_val  = float(macd_df["MACD_12_26_9"].iloc[-1])  if macd_df is not None else None
        macd_sig  = float(macd_df["MACDs_12_26_9"].iloc[-1]) if macd_df is not None else None
        macd_hist = float(macd_df["MACDh_12_26_9"].iloc[-1]) if macd_df is not None else None

        bb_df    = ta.bbands(close, length=20, std=2)
        bb_upper = float(bb_df["BBU_20_2.0"].iloc[-1]) if bb_df is not None else None
        bb_lower = float(bb_df["BBL_20_2.0"].iloc[-1]) if bb_df is not None else None
        bb_pct   = float(bb_df["BBP_20_2.0"].iloc[-1]) if bb_df is not None else None

        ema9  = float(ta.ema(close, length=9).iloc[-1])
        ema21 = float(ta.ema(close, length=21).iloc[-1])
        ema50 = float(ta.ema(close, length=min(50, len(df) - 1)).iloc[-1])

        atr_s = ta.atr(high, low, close, length=14)
        atr   = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.isna().all() else None

        stoch_df = ta.stoch(high, low, close, k=14, d=3)
        stoch_k  = float(stoch_df["STOCHk_14_3_3"].iloc[-1]) if stoch_df is not None else None

        # VWAP (intraday proxy using available bars)
        vwap = float((close * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])

        vol_sma   = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma > 0 else 1.0

        # Rate of change (5-bar momentum)
        roc5 = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else 0.0

        price   = float(close.iloc[-1])
        prev    = float(close.iloc[-2]) if len(close) > 1 else price
        chg_pct = (price - prev) / prev * 100

        ema_trend = (
            "bullish" if ema9 > ema21 > ema50
            else "bearish" if ema9 < ema21 < ema50
            else "mixed"
        )

        return {
            "price":             round(price, 4),
            "change_pct":        round(chg_pct, 3),
            "roc5":              round(roc5, 3),
            "rsi":               round(rsi, 2)       if rsi       is not None else None,
            "macd":              round(macd_val, 4)   if macd_val  is not None else None,
            "macd_signal":       round(macd_sig, 4)   if macd_sig  is not None else None,
            "macd_histogram":    round(macd_hist, 4)  if macd_hist is not None else None,
            "macd_bullish":      (macd_val > macd_sig) if (macd_val and macd_sig) else None,
            "macd_hist_growing": (macd_hist > 0)       if macd_hist is not None else None,
            "bb_upper":          round(bb_upper, 4) if bb_upper is not None else None,
            "bb_lower":          round(bb_lower, 4) if bb_lower is not None else None,
            "bb_pct":            round(bb_pct * 100, 1) if bb_pct is not None else None,
            "vwap":              round(vwap, 4),
            "above_vwap":        price > vwap,
            "ema9":              round(ema9, 4),
            "ema21":             round(ema21, 4),
            "ema50":             round(ema50, 4),
            "ema_trend":         ema_trend,
            "above_ema9":        price > ema9,
            "above_ema21":       price > ema21,
            "above_ema50":       price > ema50,
            "atr":               round(atr, 4) if atr is not None else None,
            "atr_pct":           round(atr / price * 100, 3) if atr else None,
            "volume_ratio":      round(vol_ratio, 2),
            "high_volume":       vol_ratio > 1.5,
            "institutional_vol": vol_ratio > 2.5,
            "stoch_k":           round(stoch_k, 2) if stoch_k is not None else None,
            "oversold":          (rsi < 35) if rsi else False,
            "overbought":        (rsi > 70) if rsi else False,
        }
    except Exception as e:
        return {"error": str(e)}


def mtf_trend(ind_5m: dict, ind_1h: dict, ind_1d: dict) -> str:
    scores = []
    for ind in [ind_5m, ind_1h, ind_1d]:
        t = ind.get("ema_trend")
        scores.append(1 if t == "bullish" else -1 if t == "bearish" else 0)
    s = sum(scores)
    if   s >= 2:  return "strong_bull"
    elif s == 1:  return "mild_bull"
    elif s <= -2: return "strong_bear"
    elif s == -1: return "mild_bear"
    return "neutral"


def composite_score(signal: dict, ind_5m: dict, ind_1h: dict) -> float:
    """Rank signals beyond just confidence — reward volume + momentum alignment."""
    conf       = signal.get("confidence", 0)
    vol_ratio  = ind_1h.get("volume_ratio", 1.0)
    roc        = abs(ind_5m.get("roc5", 0))
    rr         = signal.get("risk_reward_ratio", 1.0)
    bonus_vol  = min(vol_ratio / 5.0, 0.15)   # up to +15% for high volume
    bonus_mom  = min(roc / 20.0, 0.10)        # up to +10% for strong momentum
    bonus_rr   = min((rr - MIN_RR) / 10.0, 0.10)  # up to +10% for great R/R
    return conf + bonus_vol + bonus_mom + bonus_rr


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _directional_return(side: str, entry: float, price: float | None) -> float | None:
    if price is None or entry <= 0:
        return None
    raw = (price - entry) / entry * 100
    return raw if side == "buy" else -raw


def _price_at_or_after(bars: pd.DataFrame, ts: datetime) -> float | None:
    if bars.empty:
        return None
    target = pd.Timestamp(ts)
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    future = bars[bars.index >= target]
    if future.empty:
        return None
    return float(future.iloc[0]["close"])


def _outcome_score(values: list[float | None], hit_stop: bool, hit_target: bool) -> float:
    available = [v for v in values if v is not None]
    if not available:
        return 0.0
    score = sum(available) / len(available)
    if hit_target:
        score += 1.5
    if hit_stop:
        score -= 1.5
    return round(score, 3)


async def evaluate_signal_outcomes(limit: int = 40) -> dict:
    """Grade old signals against what actually happened after the call."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    res = (
        supabase.table("signals")
        .select("id,symbol,signal,entry_price,stop_loss,take_profit,created_at,outcome_checked_at")
        .in_("signal", ["buy", "sell"])
        .lte("created_at", cutoff)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )

    evaluated = 0
    skipped = 0
    errors = 0

    for sig in res.data or []:
        try:
            if sig.get("outcome_checked_at"):
                checked = _parse_ts(sig["outcome_checked_at"])
                created = _parse_ts(sig["created_at"])
                if checked >= created + timedelta(days=3):
                    skipped += 1
                    continue
                if checked > datetime.now(timezone.utc) - timedelta(hours=12):
                    skipped += 1
                    continue

            created_at = _parse_ts(sig["created_at"])
            end_at = min(datetime.now(timezone.utc), created_at + timedelta(days=4))
            bars = await fetch_historical_bars(sig["symbol"], "1Hour", created_at, end_at)
            if bars.empty:
                skipped += 1
                continue

            entry = float(sig.get("entry_price") or bars.iloc[0]["close"])
            price_1h = _price_at_or_after(bars, created_at + timedelta(hours=1))
            price_1d = _price_at_or_after(bars, created_at + timedelta(days=1))
            price_3d = _price_at_or_after(bars, created_at + timedelta(days=3))

            side = sig["signal"]
            elapsed = bars.iloc[1:] if len(bars) > 1 else bars
            if side == "buy":
                max_favorable = (float(elapsed["high"].max()) - entry) / entry * 100
                max_adverse = (float(elapsed["low"].min()) - entry) / entry * 100
                hit_stop = bool(sig.get("stop_loss") and float(elapsed["low"].min()) <= float(sig["stop_loss"]))
                hit_target = bool(sig.get("take_profit") and float(elapsed["high"].max()) >= float(sig["take_profit"]))
            else:
                max_favorable = (entry - float(elapsed["low"].min())) / entry * 100
                max_adverse = (entry - float(elapsed["high"].max())) / entry * 100
                hit_stop = bool(sig.get("stop_loss") and float(elapsed["high"].max()) >= float(sig["stop_loss"]))
                hit_target = bool(sig.get("take_profit") and float(elapsed["low"].min()) <= float(sig["take_profit"]))

            ret_1h = _directional_return(side, entry, price_1h)
            ret_1d = _directional_return(side, entry, price_1d)
            ret_3d = _directional_return(side, entry, price_3d)
            score = _outcome_score([ret_1h, ret_1d, ret_3d], hit_stop, hit_target)
            checked_at = datetime.now(timezone.utc).isoformat()

            supabase.table("signal_outcomes").upsert(
                {
                    "signal_id": sig["id"],
                    "symbol": sig["symbol"],
                    "signal": side,
                    "entry_price": round(entry, 4),
                    "price_1h": round(price_1h, 4) if price_1h is not None else None,
                    "price_1d": round(price_1d, 4) if price_1d is not None else None,
                    "price_3d": round(price_3d, 4) if price_3d is not None else None,
                    "return_1h_pct": round(ret_1h, 3) if ret_1h is not None else None,
                    "return_1d_pct": round(ret_1d, 3) if ret_1d is not None else None,
                    "return_3d_pct": round(ret_3d, 3) if ret_3d is not None else None,
                    "max_favorable_pct": round(max_favorable, 3),
                    "max_adverse_pct": round(max_adverse, 3),
                    "hit_stop": hit_stop,
                    "hit_take_profit": hit_target,
                    "outcome_score": score,
                    "checked_at": checked_at,
                },
                on_conflict="signal_id",
            ).execute()
            supabase.table("signals").update({"outcome_checked_at": checked_at}).eq("id", sig["id"]).execute()
            evaluated += 1
        except Exception as e:
            errors += 1
            await log_bot(f"Signal outcome error [{sig.get('symbol')}]: {e}", "error")

    bot_state["last_outcome_eval_at"] = datetime.now(timezone.utc).isoformat()
    return {"evaluated": evaluated, "skipped": skipped, "errors": errors}

# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------
async def get_market_regime() -> dict:
    try:
        spy_bars, qqq_bars = await asyncio.gather(
            fetch_bars("SPY", "1Hour", 50),
            fetch_bars("QQQ", "1Hour", 50),
        )
        spy = compute_indicators(spy_bars)
        qqq = compute_indicators(qqq_bars)

        regime = "neutral"
        if spy.get("ema_trend") == "bullish" and (spy.get("rsi") or 50) > 50:
            regime = "bull"
        elif spy.get("ema_trend") == "bearish" and (spy.get("rsi") or 50) < 50:
            regime = "bear"

        # Both SPY and QQQ bearish = confirmed bear
        if spy.get("ema_trend") == "bearish" and qqq.get("ema_trend") == "bearish":
            regime = "bear"

        return {
            "regime":        regime,
            "spy_rsi":       spy.get("rsi"),
            "spy_trend":     spy.get("ema_trend"),
            "spy_price":     spy.get("price"),
            "spy_change":    spy.get("change_pct"),
            "spy_vol_ratio": spy.get("volume_ratio"),
            "qqq_trend":     qqq.get("ema_trend"),
            "qqq_rsi":       qqq.get("rsi"),
        }
    except Exception:
        return {"regime": "unknown"}

# ---------------------------------------------------------------------------
# Claude AI analysis
# ---------------------------------------------------------------------------
async def analyze_with_claude(
    symbol: str,
    ind_5m: dict,
    ind_1h: dict,
    ind_1d: dict,
    news: list[dict],
    market_regime: dict,
    portfolio_value: float,
    open_positions: int,
) -> dict:
    trend = mtf_trend(ind_5m, ind_1h, ind_1d)

    news_text = "\n".join(
        f"- [{n.get('created_at', '')[:10]}] {n.get('headline', '')} (via {n.get('source', '?')})"
        for n in news[:6]
    ) or "No recent news."

    prompt = f"""You are the head trader at an elite quantitative hedge fund. You manage a high-conviction momentum portfolio. Your mandate: find asymmetric opportunities with exceptional risk/reward. You do not trade mediocre setups.

## MARKET CONTEXT
Regime: {market_regime.get('regime', 'unknown').upper()}
SPY: trend={market_regime.get('spy_trend')} | RSI={market_regime.get('spy_rsi')} | Δ={market_regime.get('spy_change')}% | vol={market_regime.get('spy_vol_ratio')}x
QQQ: trend={market_regime.get('qqq_trend')} | RSI={market_regime.get('qqq_rsi')}

## TARGET: {symbol}
Multi-Timeframe Alignment: {trend.upper()}

### 5-Minute (entry timing & immediate momentum)
{json.dumps(ind_5m, indent=2)}

### 1-Hour (trend confirmation & institutional flow)
{json.dumps(ind_1h, indent=2)}

### Daily (macro structure & key levels)
{json.dumps(ind_1d, indent=2)}

## NEWS & CATALYSTS
{news_text}

## PORTFOLIO STATE
Value: ${portfolio_value:,.0f} | Open: {open_positions}/{MAX_POSITIONS} | Risk/trade: {RISK_PER_TRADE*100}%

## SCORING CRITERIA — score each honestly
1. MTF TREND: All 3 timeframes pointing same direction? (5m+1h+daily EMAs aligned)
2. VOLUME CONFIRMATION: volume_ratio > 1.5x? Institutional participation?
3. RSI POSITIONING: Long zone 35-65 | Short zone 35-65 (avoid extremes >75 or <25)
4. MACD MOMENTUM: macd_histogram growing in trade direction?
5. PRICE STRUCTURE: Above VWAP + ema21 for longs | Below for shorts?
6. NEWS CATALYST: Does recent news provide a fundamental reason?
7. REGIME ALIGNMENT: Trading WITH the market direction?

## RULES
- Need 5+ of 7 criteria to trade. Quality > quantity.
- Minimum R/R: {MIN_RR}:1 (stop loss must be tight, target must be realistic)
- Stop loss: use ATR-based (1.0-1.5x ATR from entry)
- Take profit: minimum {MIN_RR}x the stop distance
- In BEAR regime: only take short signals or hold
- In BULL regime: prioritize long signals
- If RSI > 75 for a long setup: confidence penalty — overstretched
- High volume (>2.5x) on breakouts = institutional buying = higher confidence

Return ONLY valid JSON, no markdown fences:
{{
  "signal": "buy" | "sell" | "hold",
  "confidence": 0.0-1.0,
  "criteria_met": 0-7,
  "entry_price": number,
  "stop_loss": number,
  "take_profit": number,
  "risk_reward_ratio": number,
  "position_size_pct": 0.01-0.04,
  "reasoning": "2-3 sentences citing specific indicator values and why this is a high-conviction setup",
  "key_catalysts": ["catalyst1", "catalyst2"],
  "timeframe": "scalp|swing",
  "invalidation": "specific price level that invalidates this thesis",
  "criteria_breakdown": {{
    "mtf_trend": true|false,
    "volume_confirmation": true|false,
    "rsi_position": true|false,
    "macd_momentum": true|false,
    "price_structure": true|false,
    "news_catalyst": true|false,
    "regime_alignment": true|false
  }}
}}"""

    # Retry up to 3 times on JSON parse error
    for attempt in range(3):
        try:
            response = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except json.JSONDecodeError:
            if attempt == 2:
                return {"signal": "hold", "confidence": 0, "reasoning": "JSON parse failed after 3 attempts"}
            await asyncio.sleep(1)

    return {"signal": "hold", "confidence": 0}

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def calc_qty(portfolio_value: float, entry: float, stop_loss: float, atr: Optional[float] = None) -> int:
    risk_dollars   = portfolio_value * RISK_PER_TRADE
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        return 0
    shares     = int(risk_dollars / risk_per_share)
    max_shares = int((portfolio_value * 0.05) / entry)
    return max(1, min(shares, max_shares))

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
async def check_circuit_breaker(account: dict) -> bool:
    """Halt trading if daily loss exceeds MAX_DAILY_LOSS_PCT."""
    equity     = float(account.get("equity", 0))
    last_eq    = float(account.get("last_equity", equity))
    day_loss   = (equity - last_eq) / last_eq if last_eq > 0 else 0

    bot_state["day_pnl"] = day_loss * 100

    if day_loss < -MAX_DAILY_LOSS_PCT:
        if not bot_state["circuit_breaker_active"]:
            bot_state["circuit_breaker_active"] = True
            await log_bot(
                f"🚨 CIRCUIT BREAKER: Daily loss {day_loss*100:.2f}% exceeds limit of {MAX_DAILY_LOSS_PCT*100}%. Halting new trades.",
                "error",
            )
        return True

    bot_state["circuit_breaker_active"] = False
    return False

# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------
async def execute_signal(signal: dict, portfolio_value: float) -> Optional[dict]:
    if signal.get("signal") == "hold":
        return None
    if signal.get("confidence", 0) < MIN_CONFIDENCE:
        return None
    if signal.get("risk_reward_ratio", 0) < MIN_RR:
        return None

    symbol = signal["symbol"]
    side   = signal["signal"]
    entry  = signal["entry_price"]
    stop   = signal["stop_loss"]
    target = signal["take_profit"]

    qty = calc_qty(portfolio_value, entry, stop)
    if qty <= 0:
        return None

    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price":  str(round(stop,   2))},
        "take_profit":   {"limit_price": str(round(target, 2))},
    }

    order = await alpaca_post("/v2/orders", payload)

    supabase.table("trades").insert({
        "symbol":           symbol,
        "side":             side,
        "qty":              qty,
        "entry_price":      entry,
        "stop_loss":        stop,
        "exit_price":       target,
        "status":           "open",
        "strategy":         "ascend_elite_v3",
        "confidence_score": signal.get("confidence"),
        "ai_reasoning":     signal.get("reasoning", ""),
        "entry_at":         datetime.now(timezone.utc).isoformat(),
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }).execute()

    signal_id = signal.get("signal_id")
    if signal_id:
        supabase.table("signals").update({"executed": True}).eq("id", signal_id).execute()

    bot_state["trades_today"] += 1
    return order

# ---------------------------------------------------------------------------
# Position monitor — trailing stops + P&L sync
# ---------------------------------------------------------------------------
async def monitor_positions():
    """Run every MONITOR_INTERVAL seconds. Manage open positions actively."""
    try:
        positions_resp = await alpaca_get("/v2/positions")
        if not isinstance(positions_resp, list) or not positions_resp:
            return

        open_orders = await alpaca_get("/v2/orders?status=open&limit=200")
        if not isinstance(open_orders, list):
            open_orders = []

        for pos in positions_resp:
            symbol  = pos["symbol"]
            qty     = abs(int(float(pos["qty"])))
            side    = pos["side"]          # "long" or "short"
            entry   = float(pos["avg_entry_price"])
            current = float(pos["current_price"])
            unreal  = float(pos["unrealized_pl"])

            # Fetch original trade record for stop/target
            res = (
                supabase.table("trades")
                .select("stop_loss, exit_price, id")
                .eq("symbol", symbol)
                .eq("status", "open")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not res.data:
                continue

            trade      = res.data[0]
            orig_stop  = trade.get("stop_loss")
            orig_target = trade.get("exit_price")

            if not orig_stop:
                continue

            initial_risk = abs(entry - orig_stop)
            if initial_risk <= 0:
                continue

            # How many R multiples have we achieved?
            if side == "long":
                gain   = current - entry
                rr     = gain / initial_risk
                new_stop = None
                if rr >= 2.0:
                    new_stop = entry + initial_risk           # lock in 1R profit
                elif rr >= 1.0:
                    new_stop = entry + 0.02                   # move to break-even

                if new_stop and new_stop > orig_stop:
                    await _replace_stop(symbol, qty, "sell", new_stop, open_orders, orig_stop)

            elif side == "short":
                gain   = entry - current
                rr     = gain / initial_risk
                new_stop = None
                if rr >= 2.0:
                    new_stop = entry - initial_risk
                elif rr >= 1.0:
                    new_stop = entry - 0.02

                if new_stop and new_stop < orig_stop:
                    await _replace_stop(symbol, qty, "buy", new_stop, open_orders, orig_stop)

            # Sync live P&L to Supabase
            supabase.table("trades").update({"pnl": round(unreal, 2)}).eq("id", trade["id"]).execute()

        bot_state["last_monitor_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        await log_bot(f"Position monitor error: {e}", "error")


async def _replace_stop(
    symbol: str, qty: int, stop_side: str,
    new_stop: float, open_orders: list, old_stop: float,
):
    """Cancel existing stop order and submit a tighter one."""
    # Find stop child orders for this symbol
    stops = [
        o for o in open_orders
        if o.get("symbol") == symbol
        and o.get("type") == "stop"
        and o.get("side") == stop_side
    ]
    for o in stops:
        try:
            await alpaca_delete(f"/v2/orders/{o['id']}")
        except Exception:
            pass

    # Submit new stop
    try:
        await alpaca_post("/v2/orders", {
            "symbol":        symbol,
            "qty":           str(qty),
            "side":          stop_side,
            "type":          "stop",
            "stop_price":    str(round(new_stop, 2)),
            "time_in_force": "gtc",
        })
        await log_bot(
            f"[{symbol}] Trailing stop: ${old_stop:.2f} → ${new_stop:.2f}",
            "info",
        )
        # Update Supabase stop_loss
        supabase.table("trades").update({"stop_loss": round(new_stop, 2)}).eq("symbol", symbol).eq("status", "open").execute()
    except Exception as e:
        await log_bot(f"[{symbol}] Stop replace failed: {e}", "error")

# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------
async def update_performance():
    """Recalculate win rate and daily metrics from closed trades."""
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        res = (
            supabase.table("trades")
            .select("pnl, status")
            .eq("status", "closed")
            .execute()
        )
        if not res.data:
            return

        closed = res.data
        wins   = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        total  = len(closed)

        bot_state["win_rate"]     = round(wins / total * 100, 1) if total > 0 else 0.0
        bot_state["wins_today"]   = wins
        bot_state["losses_today"] = total - wins
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Portfolio sync
# ---------------------------------------------------------------------------
async def sync_portfolio(account: dict):
    try:
        equity = float(account.get("equity", 0))
        supabase.table("portfolio").upsert({
            "id":           1,
            "equity":       equity,
            "cash":         float(account.get("cash", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "day_pnl":      float(account.get("unrealized_pl", 0)),
            "total_pnl":    equity - 100_000,
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        await log_bot(f"Portfolio sync error: {e}", "error")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
async def log_bot(message: str, level: str = "info"):
    try:
        supabase.table("bot_logs").insert({
            "message":    message,
            "level":      level,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------
async def scan_symbol(
    symbol: str,
    regime: dict,
    portfolio_value: float,
    open_positions: int,
) -> Optional[dict]:
    try:
        bars_5m, bars_1h, bars_1d = await asyncio.gather(
            fetch_bars(symbol, "5Min",  60),
            fetch_bars(symbol, "1Hour", 60),
            fetch_bars(symbol, "1Day",  60),
        )

        if bars_5m.empty or bars_1h.empty:
            return None

        ind_5m = compute_indicators(bars_5m)
        ind_1h = compute_indicators(bars_1h)
        ind_1d = compute_indicators(bars_1d)

        if not ind_5m or "error" in ind_5m:
            return None

        news   = await fetch_news(symbol)
        signal = await analyze_with_claude(
            symbol, ind_5m, ind_1h, ind_1d,
            news, regime, portfolio_value, open_positions,
        )

        signal["symbol"]     = symbol
        signal["created_at"] = datetime.now(timezone.utc).isoformat()
        signal["strategy"]   = "ascend_elite_v3"
        signal["strength"]   = signal.get("confidence", 0)
        signal["_score"]     = composite_score(signal, ind_5m, ind_1h)

        signal_insert = supabase.table("signals").insert({
            "symbol":     symbol,
            "signal":     signal.get("signal", "hold"),
            "strength":   signal["strength"],
            "confidence": signal.get("confidence", 0),
            "criteria_met": signal.get("criteria_met"),
            "entry_price": signal.get("entry_price"),
            "stop_loss": signal.get("stop_loss"),
            "take_profit": signal.get("take_profit"),
            "risk_reward_ratio": signal.get("risk_reward_ratio"),
            "ai_reasoning": signal.get("reasoning", ""),
            "market_regime": regime,
            "composite_score": signal.get("_score", 0),
            "strategy":   "ascend_elite_v3",
            "indicators": {
                "5m":    ind_5m,
                "1h":    ind_1h,
                "1d":    ind_1d,
                "trend": mtf_trend(ind_5m, ind_1h, ind_1d),
            },
            "created_at": signal["created_at"],
        }).execute()
        if signal_insert.data:
            signal["signal_id"] = signal_insert.data[0].get("id")

        bot_state["last_signal_at"]  = signal["created_at"]
        bot_state["signals_today"]  += 1

        trend = mtf_trend(ind_5m, ind_1h, ind_1d)
        await log_bot(
            f"[{symbol}] {signal.get('signal','?').upper()} | "
            f"conf={signal.get('confidence',0):.0%} | "
            f"score={signal.get('_score',0):.2f} | "
            f"trend={trend} | rsi1h={ind_1h.get('rsi')} | "
            f"vol={ind_1h.get('volume_ratio')}x | "
            f"criteria={signal.get('criteria_met','?')}/7",
            "info",
        )
        return signal

    except Exception as e:
        await log_bot(f"[{symbol}] scan error: {e}", "error")
        return None


async def run_scan():
    if not bot_state["running"]:
        return

    try:
        n = bot_state["scan_count"] + 1
        await log_bot(f"▶ Scan #{n} | {len(WATCHLIST)} symbols", "info")

        regime, account = await asyncio.gather(
            get_market_regime(),
            alpaca_get("/v2/account"),
        )

        # Circuit breaker check
        if await check_circuit_breaker(account):
            await log_bot("Circuit breaker active — skipping trade execution this scan", "warning")

        portfolio_value = float(account.get("portfolio_value", 100_000))
        positions_resp  = await alpaca_get("/v2/positions")
        open_positions  = len(positions_resp) if isinstance(positions_resp, list) else 0

        await log_bot(
            f"Regime: {regime.get('regime','?').upper()} | "
            f"${portfolio_value:,.0f} | {open_positions}/{MAX_POSITIONS} positions open",
            "info",
        )

        # Scan all symbols in batches
        all_signals: list[dict] = []
        for i in range(0, len(WATCHLIST), BATCH_SIZE):
            if not bot_state["running"]:
                return
            batch   = WATCHLIST[i : i + BATCH_SIZE]
            results = await asyncio.gather(*[
                scan_symbol(sym, regime, portfolio_value, open_positions)
                for sym in batch
            ])
            all_signals.extend(s for s in results if s is not None)
            await asyncio.sleep(1.5)

        # Filter and rank by composite score
        actionable = sorted(
            [
                s for s in all_signals
                if s.get("signal") in ("buy", "sell")
                and s.get("confidence", 0) >= MIN_CONFIDENCE
                and s.get("risk_reward_ratio", 0) >= MIN_RR
            ],
            key=lambda x: x.get("_score", 0),
            reverse=True,
        )

        slots = MAX_POSITIONS - open_positions
        await log_bot(
            f"{len(actionable)} high-conviction signals | {slots} slots | "
            f"circuit_breaker={bot_state['circuit_breaker_active']}",
            "info",
        )

        if not bot_state["circuit_breaker_active"]:
            for signal in actionable[:slots]:
                if not bot_state["running"]:
                    break
                try:
                    order = await execute_signal(signal, portfolio_value)
                    if order:
                        await log_bot(
                            f"✅ TRADE: {signal['signal'].upper()} {signal['symbol']} | "
                            f"conf={signal['confidence']:.0%} | "
                            f"R/R={signal.get('risk_reward_ratio',0):.1f} | "
                            f"entry=${signal['entry_price']} | "
                            f"stop=${signal['stop_loss']} | "
                            f"target=${signal['take_profit']}",
                            "info",
                        )
                except Exception as e:
                    await log_bot(f"Order failed [{signal['symbol']}]: {e}", "error")

        await sync_portfolio(account)
        await update_performance()
        outcome_stats = await evaluate_signal_outcomes(limit=20)
        if outcome_stats["evaluated"]:
            await log_bot(
                f"Outcome evaluator graded {outcome_stats['evaluated']} signals "
                f"({outcome_stats['errors']} errors)",
                "info",
            )

        bot_state["scan_count"]  += 1
        bot_state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
        await log_bot(f"✓ Scan #{n} complete", "info")

    except Exception as e:
        await log_bot(f"Scan error: {e}", "error")

# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------
async def scanner_loop():
    while bot_state["running"]:
        await run_scan()
        for _ in range(SCAN_INTERVAL_SECS):
            if not bot_state["running"]:
                return
            await asyncio.sleep(1)


async def monitor_loop():
    """Separate loop: monitors positions and updates trailing stops."""
    await asyncio.sleep(30)  # brief startup delay
    while bot_state["running"]:
        await monitor_positions()
        for _ in range(MONITOR_INTERVAL):
            if not bot_state["running"]:
                return
            await asyncio.sleep(1)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_state["started_at"] = datetime.now(timezone.utc).isoformat()
    await log_bot("🚀 Ascend Trader Elite v3 initialized", "info")
    yield
    for t in [scan_task, monitor_task]:
        if t and not t.done():
            t.cancel()


app = FastAPI(title="Ascend Trader Elite", version="3.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
async def get_status():
    uptime = 0
    if bot_state["started_at"]:
        uptime = int(
            (datetime.now(timezone.utc) - datetime.fromisoformat(bot_state["started_at"])).total_seconds()
        )
    return {**bot_state, "uptime_seconds": uptime}


@app.post("/start")
async def start_bot():
    global scan_task, monitor_task
    if bot_state["running"]:
        return {"status": "already_running"}
    bot_state.update(
        running=True, circuit_breaker_active=False,
        trades_today=0, signals_today=0, scan_count=0,
    )
    scan_task    = asyncio.create_task(scanner_loop())
    monitor_task = asyncio.create_task(monitor_loop())
    await log_bot("🟢 Bot STARTED — Ascend Elite v3 active", "info")
    return {"status": "started"}


@app.post("/stop")
async def stop_bot():
    global scan_task, monitor_task
    bot_state["running"] = False
    for t in [scan_task, monitor_task]:
        if t:
            t.cancel()
    await log_bot("🔴 Bot STOPPED", "warning")
    return {"status": "stopped"}


@app.post("/scan")
async def trigger_scan():
    if not bot_state["running"]:
        raise HTTPException(400, "Bot not running. Call /start first.")
    asyncio.create_task(run_scan())
    return {"status": "scan_triggered"}


@app.get("/account")
async def get_account():
    return await alpaca_get("/v2/account")


@app.get("/positions")
async def get_positions():
    return await alpaca_get("/v2/positions")


@app.get("/performance")
async def get_performance():
    await update_performance()
    return {
        "win_rate":    bot_state["win_rate"],
        "trades_today": bot_state["trades_today"],
        "signals_today": bot_state["signals_today"],
        "day_pnl_pct":  bot_state["day_pnl"],
        "scan_count":   bot_state["scan_count"],
    }


@app.post("/signals/evaluate")
async def evaluate_signals(limit: int = 40):
    try:
        result = await evaluate_signal_outcomes(limit=limit)
        await log_bot(
            f"Manual outcome evaluation | graded={result['evaluated']} | "
            f"skipped={result['skipped']} | errors={result['errors']}",
            "info" if result["errors"] == 0 else "warning",
        )
        return result
    except Exception as e:
        await log_bot(f"Outcome evaluation failed: {e}", "error")
        raise HTTPException(500, str(e))


@app.get("/signal-outcomes")
async def list_signal_outcomes(limit: int = 100):
    res = (
        supabase.table("signal_outcomes")
        .select("*")
        .order("checked_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


@app.post("/backtest")
async def create_backtest(payload: dict | None = None):
    """Run a deterministic research backtest and persist the full run."""
    try:
        config = parse_config(payload)
        result = await run_research_backtest(config, supabase)
        await log_bot(
            f"Backtest complete | {result['total_trades']} trades | "
            f"return={result['total_return_pct']:.2f}% | "
            f"dd={result['max_drawdown_pct']:.2f}% | "
            f"pf={result['profit_factor']:.2f}",
            "info",
        )
        return result
    except Exception as e:
        await log_bot(f"Backtest failed: {e}", "error")
        raise HTTPException(500, str(e))


@app.get("/backtests")
async def list_backtests(limit: int = 10):
    res = (
        supabase.table("backtest_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


@app.get("/backtests/{run_id}/trades")
async def list_backtest_trades(run_id: str, limit: int = 100):
    res = (
        supabase.table("backtest_trades")
        .select("*")
        .eq("run_id", run_id)
        .order("entry_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


@app.delete("/order/{order_id}")
async def cancel_order(order_id: str):
    success = await alpaca_delete(f"/v2/orders/{order_id}")
    if not success:
        raise HTTPException(400, "Cancel failed")
    return {"cancelled": order_id}
