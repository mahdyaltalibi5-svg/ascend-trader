"""
Ascend Trader Elite — AI-powered algorithmic trading bot.
Multi-timeframe technical analysis + news sentiment + Claude AI.
"""

import os
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WATCHLIST = [
    # Mega caps
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    # High momentum tech
    "AMD", "PLTR", "COIN", "HOOD", "MSTR", "CRWD", "PANW",
    # ETFs for macro view
    "SPY", "QQQ", "IWM",
    # Volatile opportunities
    "SMCI", "ARM", "SOFI", "RIVN",
]

MAX_POSITIONS      = 5
RISK_PER_TRADE     = 0.015   # risk 1.5% of portfolio per trade
MIN_CONFIDENCE     = 0.72    # only trade signals above 72% confidence
SCAN_INTERVAL_SECS = 300     # scan every 5 minutes
BATCH_SIZE         = 5       # symbols per concurrent batch (rate limit safety)

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
    "running":        False,
    "strategy":       "ascend_elite",
    "last_scan_at":   None,
    "last_signal_at": None,
    "trades_today":   0,
    "signals_today":  0,
    "win_rate":       0.0,
    "started_at":     None,
    "scan_count":     0,
}

scan_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------
async def alpaca_get(path: str, base: str = ALPACA_TRADE_URL) -> dict:
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

        rsi_s  = ta.rsi(close, length=14)
        rsi    = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.isna().all() else None

        macd_df   = ta.macd(close, fast=12, slow=26, signal=9)
        macd_val  = float(macd_df["MACD_12_26_9"].iloc[-1])  if macd_df is not None else None
        macd_sig  = float(macd_df["MACDs_12_26_9"].iloc[-1]) if macd_df is not None else None
        macd_hist = float(macd_df["MACDh_12_26_9"].iloc[-1]) if macd_df is not None else None

        bb_df     = ta.bbands(close, length=20, std=2)
        bb_upper  = float(bb_df["BBU_20_2.0"].iloc[-1]) if bb_df is not None else None
        bb_lower  = float(bb_df["BBL_20_2.0"].iloc[-1]) if bb_df is not None else None
        bb_pct    = float(bb_df["BBP_20_2.0"].iloc[-1]) if bb_df is not None else None

        ema9  = float(ta.ema(close, length=9).iloc[-1])
        ema21 = float(ta.ema(close, length=21).iloc[-1])
        ema50 = float(ta.ema(close, length=min(50, len(df) - 1)).iloc[-1])

        atr_s = ta.atr(high, low, close, length=14)
        atr   = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.isna().all() else None

        stoch_df = ta.stoch(high, low, close, k=14, d=3)
        stoch_k  = float(stoch_df["STOCHk_14_3_3"].iloc[-1]) if stoch_df is not None else None

        vol_sma   = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma > 0 else 1.0

        price     = float(close.iloc[-1])
        prev      = float(close.iloc[-2]) if len(close) > 1 else price
        chg_pct   = (price - prev) / prev * 100

        ema_trend = (
            "bullish" if ema9 > ema21 > ema50
            else "bearish" if ema9 < ema21 < ema50
            else "mixed"
        )

        return {
            "price":          round(price, 4),
            "change_pct":     round(chg_pct, 3),
            "rsi":            round(rsi, 2)    if rsi    is not None else None,
            "macd":           round(macd_val, 4)  if macd_val  is not None else None,
            "macd_signal":    round(macd_sig, 4)  if macd_sig  is not None else None,
            "macd_histogram": round(macd_hist, 4) if macd_hist is not None else None,
            "macd_bullish":   (macd_val > macd_sig) if (macd_val and macd_sig) else None,
            "bb_upper":       round(bb_upper, 4) if bb_upper is not None else None,
            "bb_lower":       round(bb_lower, 4) if bb_lower is not None else None,
            "bb_position_pct": round(bb_pct * 100, 1) if bb_pct is not None else None,
            "ema9":           round(ema9, 4),
            "ema21":          round(ema21, 4),
            "ema50":          round(ema50, 4),
            "ema_trend":      ema_trend,
            "above_ema9":     price > ema9,
            "above_ema21":    price > ema21,
            "above_ema50":    price > ema50,
            "atr":            round(atr, 4)   if atr   is not None else None,
            "atr_pct":        round(atr / price * 100, 3) if atr else None,
            "volume_ratio":   round(vol_ratio, 2),
            "high_volume":    vol_ratio > 1.5,
            "stoch_k":        round(stoch_k, 2) if stoch_k is not None else None,
            "oversold":       rsi < 35 if rsi else False,
            "overbought":     rsi > 70 if rsi else False,
        }
    except Exception as e:
        return {"error": str(e)}


def mtf_trend(ind_5m: dict, ind_1h: dict, ind_1d: dict) -> str:
    scores = []
    for ind in [ind_5m, ind_1h, ind_1d]:
        if ind.get("ema_trend") == "bullish":
            scores.append(1)
        elif ind.get("ema_trend") == "bearish":
            scores.append(-1)
        else:
            scores.append(0)
    s = sum(scores)
    if   s >= 2:  return "strong_bull"
    elif s == 1:  return "mild_bull"
    elif s <= -2: return "strong_bear"
    elif s == -1: return "mild_bear"
    return "neutral"

# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------
async def get_market_regime() -> dict:
    try:
        spy_bars = await fetch_bars("SPY", "1Hour", 50)
        ind      = compute_indicators(spy_bars)
        regime   = "neutral"
        if ind.get("ema_trend") == "bullish" and (ind.get("rsi") or 50) > 50:
            regime = "bull"
        elif ind.get("ema_trend") == "bearish" and (ind.get("rsi") or 50) < 50:
            regime = "bear"
        return {
            "regime":        regime,
            "spy_rsi":       ind.get("rsi"),
            "spy_trend":     ind.get("ema_trend"),
            "spy_price":     ind.get("price"),
            "spy_change":    ind.get("change_pct"),
            "spy_vol_ratio": ind.get("volume_ratio"),
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

    prompt = f"""You are an elite quantitative hedge fund trader with 20 years of experience. Your mandate is exceptional risk-adjusted returns. Analyze this opportunity and make a decisive, data-driven trading decision.

## MARKET CONTEXT
Regime: {market_regime.get('regime', 'unknown').upper()} | SPY trend: {market_regime.get('spy_trend')} | SPY RSI: {market_regime.get('spy_rsi')} | SPY Δ: {market_regime.get('spy_change')}%

## SYMBOL: {symbol}
Multi-Timeframe Alignment: {trend.upper()}

### 5-Minute (momentum / entry timing)
{json.dumps(ind_5m, indent=2)}

### 1-Hour (trend confirmation)
{json.dumps(ind_1h, indent=2)}

### Daily (macro structure / key levels)
{json.dumps(ind_1d, indent=2)}

## NEWS & CATALYSTS (last 24h)
{news_text}

## PORTFOLIO
Value: ${portfolio_value:,.0f} | Open positions: {open_positions}/{MAX_POSITIONS} | Risk/trade: {RISK_PER_TRADE*100}%

## ANALYSIS CRITERIA
Score each factor before deciding:
1. MTF trend alignment — are 5m + 1h + daily all pointing the same direction?
2. Volume confirmation — is volume_ratio > 1.5? (Institutional participation)
3. RSI positioning — long: 40-65, short: 35-60 (avoid extremes)
4. MACD momentum — histogram expanding in trade direction?
5. Bollinger position — near lower band for longs, upper for shorts?
6. News sentiment — does news support or oppose the directional bias?
7. Market regime — trading with or against the broader market?

ONLY trade when at least 5 of 7 criteria align. Quality over quantity.

## RESPONSE — return ONLY valid JSON, no markdown:
{{
  "signal": "buy" | "sell" | "hold",
  "confidence": 0.0-1.0,
  "entry_price": number,
  "stop_loss": number,
  "take_profit": number,
  "risk_reward_ratio": number,
  "position_size_pct": 0.01-0.05,
  "reasoning": "2-3 sentence professional analysis citing specific indicator values",
  "key_catalysts": ["catalyst1", "catalyst2"],
  "timeframe": "scalp|swing",
  "invalidation": "specific price level or condition that invalidates thesis"
}}

If fewer than 5 criteria align, return signal: "hold". Never force a trade. Patience is edge."""

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    return json.loads(text)

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def calc_qty(portfolio_value: float, entry: float, stop_loss: float) -> int:
    risk_dollars   = portfolio_value * RISK_PER_TRADE
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        return 0
    shares     = int(risk_dollars / risk_per_share)
    max_shares = int((portfolio_value * 0.05) / entry)
    return max(1, min(shares, max_shares))

# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------
async def execute_signal(signal: dict, portfolio_value: float) -> Optional[dict]:
    if signal.get("signal") == "hold":
        return None
    if signal.get("confidence", 0) < MIN_CONFIDENCE:
        return None

    symbol    = signal["symbol"]
    side      = signal["signal"]
    entry     = signal["entry_price"]
    stop      = signal["stop_loss"]
    target    = signal["take_profit"]

    qty = calc_qty(portfolio_value, entry, stop)
    if qty <= 0:
        return None

    # Bracket order: market entry + automatic stop loss + take profit
    payload = {
        "symbol":         symbol,
        "qty":            str(qty),
        "side":           side,
        "type":           "market",
        "time_in_force":  "day",
        "order_class":    "bracket",
        "stop_loss":      {"stop_price":   str(round(stop,   2))},
        "take_profit":    {"limit_price":  str(round(target, 2))},
    }

    order = await alpaca_post("/v2/orders", payload)

    supabase.table("trades").insert({
        "symbol":           symbol,
        "side":             side,
        "qty":              qty,
        "entry_price":      entry,
        "stop_loss":        stop,
        "status":           "open",
        "strategy":         "ascend_elite",
        "confidence_score": signal["confidence"],
        "ai_reasoning":     signal.get("reasoning", ""),
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }).execute()

    bot_state["trades_today"] += 1
    return order

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
        signal["strategy"]   = "ascend_elite"
        signal["strength"]   = signal.get("confidence", 0)

        supabase.table("signals").insert({
            "symbol":     symbol,
            "signal":     signal["signal"],
            "strength":   signal["strength"],
            "strategy":   "ascend_elite",
            "indicators": {
                "5m":    ind_5m,
                "1h":    ind_1h,
                "1d":    ind_1d,
                "trend": mtf_trend(ind_5m, ind_1h, ind_1d),
            },
            "created_at": signal["created_at"],
        }).execute()

        bot_state["last_signal_at"] = signal["created_at"]
        bot_state["signals_today"]  += 1

        trend = mtf_trend(ind_5m, ind_1h, ind_1d)
        await log_bot(
            f"[{symbol}] {signal['signal'].upper()} | conf={signal['confidence']:.0%} "
            f"| trend={trend} | rsi={ind_1h.get('rsi')} | vol={ind_1h.get('volume_ratio')}x",
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
        await log_bot(f"▶ Scan #{n} starting — {len(WATCHLIST)} symbols", "info")

        regime, account = await asyncio.gather(
            get_market_regime(),
            alpaca_get("/v2/account"),
        )

        portfolio_value = float(account.get("portfolio_value", 100_000))
        positions_resp  = await alpaca_get("/v2/positions")
        open_positions  = len(positions_resp) if isinstance(positions_resp, list) else 0

        await log_bot(
            f"Regime: {regime['regime'].upper()} | "
            f"Portfolio: ${portfolio_value:,.0f} | "
            f"Positions: {open_positions}/{MAX_POSITIONS}",
            "info",
        )

        # Scan in batches to respect rate limits
        all_signals: list[dict] = []
        for i in range(0, len(WATCHLIST), BATCH_SIZE):
            batch   = WATCHLIST[i : i + BATCH_SIZE]
            results = await asyncio.gather(*[
                scan_symbol(sym, regime, portfolio_value, open_positions)
                for sym in batch
            ])
            all_signals.extend(s for s in results if s is not None)
            await asyncio.sleep(1.5)

        # Filter and rank actionable signals
        actionable = sorted(
            [s for s in all_signals if s.get("signal") in ("buy", "sell") and s.get("confidence", 0) >= MIN_CONFIDENCE],
            key=lambda x: x["confidence"],
            reverse=True,
        )

        slots = MAX_POSITIONS - open_positions
        await log_bot(f"Found {len(actionable)} high-confidence signals | {slots} slots available", "info")

        for signal in actionable[:slots]:
            if not bot_state["running"]:
                break
            try:
                order = await execute_signal(signal, portfolio_value)
                if order:
                    await log_bot(
                        f"✅ TRADE: {signal['signal'].upper()} {signal['symbol']} "
                        f"| conf={signal['confidence']:.0%} "
                        f"| entry=${signal['entry_price']} "
                        f"| stop=${signal['stop_loss']} "
                        f"| target=${signal['take_profit']}",
                        "info",
                    )
            except Exception as e:
                await log_bot(f"Order failed [{signal['symbol']}]: {e}", "error")

        await sync_portfolio(account)

        bot_state["scan_count"]   += 1
        bot_state["last_scan_at"]  = datetime.now(timezone.utc).isoformat()
        await log_bot(f"✓ Scan #{n} complete — {len(actionable)} signals acted on", "info")

    except Exception as e:
        await log_bot(f"Scan loop error: {e}", "error")


async def scanner_loop():
    while bot_state["running"]:
        await run_scan()
        for _ in range(SCAN_INTERVAL_SECS):
            if not bot_state["running"]:
                return
            await asyncio.sleep(1)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_state["started_at"] = datetime.now(timezone.utc).isoformat()
    await log_bot("Ascend Trader Elite v2 initialized", "info")
    yield
    if scan_task and not scan_task.done():
        scan_task.cancel()


app = FastAPI(title="Ascend Trader Elite", version="2.0.0", lifespan=lifespan)
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
        started = datetime.fromisoformat(bot_state["started_at"])
        uptime  = int((datetime.now(timezone.utc) - started).total_seconds())
    return {**bot_state, "uptime_seconds": uptime}


@app.post("/start")
async def start_bot():
    global scan_task
    if bot_state["running"]:
        return {"status": "already_running"}
    bot_state.update(running=True, trades_today=0, signals_today=0, scan_count=0)
    scan_task = asyncio.create_task(scanner_loop())
    await log_bot("🚀 Bot STARTED — Ascend Elite strategy active", "info")
    return {"status": "started"}


@app.post("/stop")
async def stop_bot():
    global scan_task
    bot_state["running"] = False
    if scan_task:
        scan_task.cancel()
    await log_bot("⏹ Bot STOPPED", "warning")
    return {"status": "stopped"}


@app.post("/scan")
async def trigger_scan():
    if not bot_state["running"]:
        raise HTTPException(400, "Bot is not running. Call /start first.")
    asyncio.create_task(run_scan())
    return {"status": "scan_triggered"}


@app.get("/account")
async def get_account():
    return await alpaca_get("/v2/account")


@app.get("/positions")
async def get_positions():
    return await alpaca_get("/v2/positions")


@app.delete("/order/{order_id}")
async def cancel_order(order_id: str):
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{ALPACA_TRADE_URL}/v2/orders/{order_id}", headers=ALPACA_HEADERS
        )
        if r.status_code not in (200, 204):
            raise HTTPException(r.status_code, r.text)
    return {"cancelled": order_id}
