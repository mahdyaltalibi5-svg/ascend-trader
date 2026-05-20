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
from zoneinfo import ZoneInfo

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
from risk_engine import validate_trade, kelly_fraction
from earnings import get_pre_earnings_opportunities
from institutional import get_cached_intel, build_institutional_context, institutional_signal_boost
from no_trade_calendar import get_calendar_context, should_trade_now
from regime_brain import get_full_regime, trading_rules_for_regime
from setup_classifier import classify_setup, score_setup_quality, setup_notes_for_claude
from relative_strength import get_relative_strength_intel, build_rs_prompt_section, rs_score_boost
from catalyst_stack import build_catalyst_score, build_catalyst_prompt_section, catalyst_confidence_boost, minimum_catalyst_threshold
from insider_flow import get_insider_intel, build_insider_prompt_section, insider_confidence_boost, InsiderIntel
from options_flow import get_options_flow_intel, build_options_flow_prompt_section, options_flow_confidence_boost
from short_interest import get_short_interest_intel, build_short_interest_prompt_section, short_interest_confidence_boost
from signal_memory import (
    build_memory_from_outcomes,
    build_memory_prompt_section,
    get_setup_historical_accuracy,
    get_learning_brief,
    get_symbols_to_avoid,
    get_weakness_report,
)
from signal_attribution import (
    build_attribution_row,
    get_component_stats,
    build_attribution_prompt_section,
    load_attribution_rows,
)

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
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

supabase: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    if SUPABASE_URL and SUPABASE_SERVICE_KEY and "your-" not in SUPABASE_SERVICE_KEY
    else None
)
claude = (
    anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    if ANTHROPIC_API_KEY and "your-" not in ANTHROPIC_API_KEY
    else None
)

ALPACA_TRADE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL  = "https://data.alpaca.markets"
ALPACA_HEADERS   = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


def require_supabase() -> Client:
    if supabase is None:
        raise HTTPException(500, "Supabase service role key is not configured")
    return supabase


def require_alpaca_keys():
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise HTTPException(500, "Alpaca API keys are not configured")

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
    "options_mode":            False,
    "no_trade_active":         False,
    "no_trade_reason":         None,
    "advanced_regime":         "unknown",
    "regime_confidence":       0.0,
    # no_trade_mode: "strict" (block execution), "balanced" (warn but allow),
    # "monitor_only" (never execute, always observe)
    "no_trade_mode":           "strict",
}

scan_task:    Optional[asyncio.Task] = None
monitor_task: Optional[asyncio.Task] = None

# Track trade IDs that have already had a partial-profit close so we don't
# fire the partial-take twice on the same position.
_partial_taken: set[str] = set()
EASTERN_TZ = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Scan event writer
# ---------------------------------------------------------------------------

async def write_scan_event(
    scan_id: str,
    symbol: str,
    stage: str,
    *,
    action: str | None = None,
    confidence: float | None = None,
    composite_score: float | None = None,
    setup_type: str | None = None,
    setup_quality: float | None = None,
    catalyst_score: float | None = None,
    rs_signal: str | None = None,
    risk_status: str | None = None,
    rejection_reason: str | None = None,
    payload: dict | None = None,
) -> None:
    """Write a structured scan event row to Supabase. Non-blocking — errors are logged only."""
    try:
        db = require_supabase()
        db.table("scan_events").insert({
            "scan_id":          scan_id,
            "symbol":           symbol,
            "stage":            stage,
            "action":           action,
            "confidence":       confidence,
            "composite_score":  composite_score,
            "setup_type":       setup_type,
            "setup_quality":    setup_quality,
            "catalyst_score":   catalyst_score,
            "rs_signal":        rs_signal,
            "risk_status":      risk_status,
            "rejection_reason": rejection_reason,
            "payload":          payload or {},
        }).execute()
    except Exception as exc:
        pass  # never crash a scan over a telemetry write

# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------
async def alpaca_get(path: str, base: str = ALPACA_TRADE_URL) -> dict | list:
    require_alpaca_keys()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{base}{path}", headers=ALPACA_HEADERS)
        r.raise_for_status()
        return r.json()


async def alpaca_post(path: str, payload: dict) -> dict:
    require_alpaca_keys()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{ALPACA_TRADE_URL}{path}", json=payload, headers=ALPACA_HEADERS
        )
        r.raise_for_status()
        return r.json()


async def alpaca_delete(path: str) -> bool:
    require_alpaca_keys()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.delete(f"{ALPACA_TRADE_URL}{path}", headers=ALPACA_HEADERS)
        return r.status_code in (200, 204)


async def check_spread_slippage(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
) -> tuple[bool, str]:
    """
    Fetch latest quote and reject if spread is too wide or estimated slippage
    breaks the trade's R/R.

    Returns (approved: bool, reason: str).
    """
    try:
        data = await alpaca_get(f"/v2/stocks/{symbol}/quotes/latest", base=ALPACA_DATA_URL)
        quote = data.get("quote", data)
        bid = float(quote.get("bp", 0) or 0)
        ask = float(quote.get("ap", 0) or 0)

        if bid <= 0 or ask <= 0:
            return True, ""  # no quote data — allow through

        mid   = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else 0

        # Reject if spread > 0.5% of mid price — too wide to trade cleanly
        if spread_pct > 0.005:
            return False, f"Spread too wide: {spread_pct*100:.2f}% ({bid:.2f}/{ask:.2f})"

        # Estimate slippage: assume crossing the spread in the trade direction.
        # If slippage eats > 20% of the R, kill the trade
        risk_per_share = abs(entry_price - stop_loss)
        slippage = (ask - mid) if side.lower() in ("buy", "long") else (mid - bid)
        if risk_per_share > 0 and slippage / risk_per_share > 0.20:
            return False, (
                f"Slippage risk too high: spread costs {slippage:.3f} "
                f"vs risk {risk_per_share:.3f} per share ({slippage/risk_per_share*100:.0f}% of R)"
            )

        return True, ""
    except Exception as exc:
        return True, f"Spread check skipped: {exc}"

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

        # Session VWAP
        vwap = float((close * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])

        # Anchored VWAPs — weekly (last 5 bars) and monthly (last 21 bars)
        def _anchored_vwap(n: int) -> float | None:
            if len(close) < n:
                return None
            c_slice = close.iloc[-n:]
            v_slice = volume.iloc[-n:]
            denom   = float(v_slice.sum())
            return float((c_slice * v_slice).sum() / denom) if denom > 0 else None

        vwap_weekly  = _anchored_vwap(5)
        vwap_monthly = _anchored_vwap(21)

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
            "vwap_weekly":       round(vwap_weekly,  4) if vwap_weekly  else None,
            "vwap_monthly":      round(vwap_monthly, 4) if vwap_monthly else None,
            "above_vwap_weekly": (price > vwap_weekly)  if vwap_weekly  else None,
            "above_vwap_monthly":(price > vwap_monthly) if vwap_monthly else None,
            "pct_from_vwap_weekly":  round((price - vwap_weekly)  / vwap_weekly  * 100, 2) if vwap_weekly  else None,
            "pct_from_vwap_monthly": round((price - vwap_monthly) / vwap_monthly * 100, 2) if vwap_monthly else None,
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


def composite_score(
    signal: dict,
    ind_5m: dict,
    ind_1h: dict,
    intel: dict | None = None,
) -> float:
    """Rank signals: confidence + volume + momentum + R/R + institutional flow."""
    conf       = signal.get("confidence", 0)
    vol_ratio  = ind_1h.get("volume_ratio", 1.0)
    roc        = abs(ind_5m.get("roc5", 0))
    rr         = signal.get("risk_reward_ratio", 1.0)
    symbol     = signal.get("symbol", "")
    side       = signal.get("signal", "hold")

    bonus_vol   = min(vol_ratio / 5.0, 0.15)
    bonus_mom   = min(roc / 20.0, 0.10)
    bonus_rr    = min((rr - MIN_RR) / 10.0, 0.10)
    bonus_intel = institutional_signal_boost(intel or {}, symbol, side)

    return conf + bonus_vol + bonus_mom + bonus_rr + bonus_intel


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
    db = require_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    res = (
        db.table("signals")
        .select(
            "id,symbol,signal,strategy,strength,confidence,criteria_met,"
            "entry_price,stop_loss,take_profit,created_at,outcome_checked_at,"
            "market_regime,indicators"
        )
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
            stop = float(sig.get("stop_loss") or entry)
            risk_per_share = abs(entry - stop)
            if price_1d is not None and risk_per_share > 0:
                r_multiple = (
                    (price_1d - entry) / risk_per_share
                    if side == "buy"
                    else (entry - price_1d) / risk_per_share
                )
            else:
                r_multiple = 0.0

            db.table("signal_outcomes").upsert(
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
            db.table("signals").update({"outcome_checked_at": checked_at}).eq("id", sig["id"]).execute()
            evaluated += 1

            # Write attribution row — non-fatal if attribution table not yet migrated
            try:
                outcome_row = {
                    "outcome_score": score,
                    "r_multiple": round(r_multiple, 3),
                    "hit_stop": hit_stop,
                    "hit_take_profit": hit_target,
                }
                attr_row = build_attribution_row(
                    signal_id=sig["id"],
                    symbol=sig["symbol"],
                    signal_record=sig,
                    outcome_record=outcome_row,
                )
                db.table("signal_attribution").upsert(
                    attr_row, on_conflict="signal_id"
                ).execute()
            except Exception:
                pass  # attribution is telemetry — never crash for it

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
        spy_bars, qqq_bars, advanced = await asyncio.gather(
            fetch_bars("SPY", "1Hour", 50),
            fetch_bars("QQQ", "1Hour", 50),
            get_full_regime(ALPACA_HEADERS, ALPACA_DATA_URL),
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

        bot_state["advanced_regime"] = advanced.get("regime", "unknown")
        bot_state["regime_confidence"] = advanced.get("confidence", 0.0)

        return {
            "regime":        regime,
            "advanced_regime": advanced.get("regime", "unknown"),
            "regime_confidence": advanced.get("confidence", 0.0),
            "leading_sector": advanced.get("leading_sector"),
            "regime_note": advanced.get("regime_note"),
            "regime_rules": trading_rules_for_regime(advanced.get("regime", "unknown")),
            "spy_rsi":       spy.get("rsi"),
            "spy_trend":     spy.get("ema_trend"),
            "spy_price":     spy.get("price"),
            "spy_change":    spy.get("change_pct"),
            "spy_vol_ratio": spy.get("volume_ratio"),
            "qqq_trend":     qqq.get("ema_trend"),
            "qqq_rsi":       qqq.get("rsi"),
        }
    except Exception as exc:
        return {
            "regime": "unknown",
            "advanced_regime": "unknown",
            "regime_confidence": 0.0,
            "regime_note": f"Regime detection failed: {exc}",
            "regime_rules": trading_rules_for_regime("unknown"),
        }


def _exceptional_signal(signal: dict) -> bool:
    """True when a signal is strong enough to survive defensive portfolio gates."""
    confidence = float(signal.get("confidence", 0) or 0)
    catalyst_score = float(signal.get("catalyst_score", 0) or 0)
    rs_signal = str(signal.get("rs_signal") or "")
    return (
        confidence >= 0.90
        and catalyst_score >= 0.60
        and (signal.get("earnings_catalyst") or rs_signal == "leader")
    )


def apply_portfolio_regime_gate(signal: dict, regime: dict, regime_rules: dict) -> tuple[bool, str]:
    """
    Scan-level portfolio throttle.

    Symbol analysis can still find good setups, but this gate decides whether
    the portfolio should take new risk in this market tape.
    """
    advanced_regime = regime.get("advanced_regime") or regime.get("regime", "unknown")
    action = signal.get("signal", "hold")
    setup_type = signal.get("setup_type") or "unknown"
    confidence = float(signal.get("confidence", 0) or 0)
    catalyst_score = float(signal.get("catalyst_score", 0) or 0)
    min_confidence = max(
        float(signal.get("min_confidence_required", MIN_CONFIDENCE) or MIN_CONFIDENCE),
        float(regime_rules.get("confidence_threshold", MIN_CONFIDENCE) or MIN_CONFIDENCE),
    )
    size_multiplier = float(regime_rules.get("size_multiplier", 1.0) or 1.0)
    avoid_setups = set(regime_rules.get("avoid_setups") or [])
    exceptional = _exceptional_signal(signal)

    signal["min_confidence_required"] = min_confidence
    signal["regime_size_multiplier"] = max(0.25, min(1.25, size_multiplier))
    signal["portfolio_gate"] = {
        "regime": advanced_regime,
        "confidence_threshold": min_confidence,
        "size_multiplier": signal["regime_size_multiplier"],
        "rule_note": regime_rules.get("note", ""),
        "exceptional_override": exceptional,
    }

    if action not in ("buy", "sell"):
        return False, "not actionable"
    if confidence < min_confidence:
        return False, f"confidence {confidence:.0%} below portfolio threshold {min_confidence:.0%}"
    if setup_type in avoid_setups and not exceptional:
        return False, f"{setup_type} is on the avoid list for {advanced_regime}"
    if advanced_regime in ("high_vol_panic", "unknown") and not exceptional:
        return False, f"{advanced_regime} requires an exceptional catalyst/RS signal"
    if advanced_regime == "risk_off" and action == "buy" and not exceptional:
        return False, "risk-off tape blocks ordinary long entries"
    if advanced_regime in ("risk_on", "trend_day") and action == "sell" and confidence < 0.85:
        return False, f"{advanced_regime} requires 85%+ confidence for shorts"
    if advanced_regime == "sector_rotation" and action == "buy":
        rs_signal = str(signal.get("rs_signal") or "")
        if rs_signal not in ("leader", "strong_leader") and catalyst_score < 0.65:
            return False, "sector rotation only allows leaders or catalyst-heavy longs"

    return True, "approved by portfolio regime gate"


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
    intel: dict | None = None,
    memory_context: str = "",
    setup_type: str = "unknown",
    setup_quality: float = 0.0,
    setup_confidence: float = 0.0,
    calendar_context: str = "",
    learning_brief: str = "",
    options_context: str = "",
    short_context: str = "",
    rs_context: str = "",
    insider_context: str = "",
) -> dict:
    if claude is None:
        return {
            "signal": "hold",
            "confidence": 0,
            "criteria_met": 0,
            "reasoning": "Anthropic API key is not configured",
        }

    trend = mtf_trend(ind_5m, ind_1h, ind_1d)

    news_text = "\n".join(
        f"- [{n.get('created_at', '')[:10]}] {n.get('headline', '')} (via {n.get('source', '?')})"
        for n in news[:6]
    ) or "No recent news."

    institutional_text = build_institutional_context(intel or {}, symbol)
    setup_text = setup_notes_for_claude(setup_type)
    calendar_text = calendar_context or get_calendar_context()

    prompt = f"""You are the head trader at an elite quantitative hedge fund. You manage a high-conviction momentum portfolio. Your mandate: find asymmetric opportunities with exceptional risk/reward. You do not trade mediocre setups.

## BOT SELF-KNOWLEDGE (WHAT I HAVE LEARNED FROM MY OWN TRADE HISTORY)
{learning_brief or "No learning brief available yet — early learning phase."}

## MARKET CONTEXT
Regime: {market_regime.get('regime', 'unknown').upper()}
SPY: trend={market_regime.get('spy_trend')} | RSI={market_regime.get('spy_rsi')} | Δ={market_regime.get('spy_change')}% | vol={market_regime.get('spy_vol_ratio')}x
QQQ: trend={market_regime.get('qqq_trend')} | RSI={market_regime.get('qqq_rsi')}

## SMART MONEY POSITIONING
{institutional_text}

## MACRO / SESSION RISK CALENDAR
{calendar_text}

## SETUP CLASSIFICATION
Setup type: {setup_type}
Classifier confidence: {setup_confidence:.2f}/1.0
Setup quality in current regime: {setup_quality:.2f}/1.0
{setup_text}

## SIGNAL MEMORY
{memory_context or "SIGNAL MEMORY: No historical memory context available."}

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

## RELATIVE STRENGTH
{rs_context or "No RS data."}

## OPTIONS FLOW (Institutional Options Activity)
{options_context or "No options flow data."}

## SHORT INTEREST / SQUEEZE POTENTIAL
{short_context or "No short interest data."}

## INSIDER FLOW (Form 4 Open-Market Purchases)
{insider_context or "No insider data."}

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
- If the calendar/session risk says do not trade, return HOLD unless the prompt explicitly says monitoring only.
- If setup quality is below 0.45, return HOLD unless every other factor is exceptional.
- If signal memory says this symbol/setup is historically over-confident, lower confidence.
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


async def ai_risk_officer(
    symbol: str,
    trader_signal: dict,
    ind_1h: dict,
    ind_1d: dict,
    news: list[dict],
    market_regime: dict,
    intel: dict | None = None,
    rs_context: str = "",
    catalyst_context: str = "",
    insider_context: str = "",
    options_context: str = "",
    short_context: str = "",
) -> tuple[bool, str]:
    """
    Second independent Claude call. Adversarial — tries to find every reason
    NOT to take this trade. Returns (approved, rejection_reason).
    Only called when trader signal is buy/sell with confidence >= MIN_CONFIDENCE.
    """
    if claude is None:
        return True, ""

    side   = trader_signal.get("signal", "hold")
    entry  = trader_signal.get("entry_price", 0)
    stop   = trader_signal.get("stop_loss", 0)
    target = trader_signal.get("take_profit", 0)
    conf   = trader_signal.get("confidence", 0)
    rr     = trader_signal.get("risk_reward_ratio", 0)

    prompt = f"""You are the Chief Risk Officer at an elite hedge fund. The head trader wants to place this trade. Your job is to VETO bad trades — not to be nice.

## PROPOSED TRADE
Symbol: {symbol}
Direction: {side.upper()}
Entry: ${entry} | Stop: ${stop} | Target: ${target}
Trader confidence: {conf:.0%} | R/R: {rr:.1f}
Trader reasoning: {trader_signal.get("reasoning", "none")}

## MARKET REGIME
{market_regime.get("regime", "unknown").upper()} | SPY RSI: {market_regime.get("spy_rsi")} | QQQ trend: {market_regime.get("qqq_trend")}

## KEY INDICATORS (1H)
RSI: {ind_1h.get("rsi")} | MACD histogram: {ind_1h.get("macd_histogram")} | Volume ratio: {ind_1h.get("volume_ratio")}x | EMA trend: {ind_1h.get("ema_trend")} | BB position: {ind_1h.get("bb_pct")}%

## DAILY STRUCTURE
RSI: {ind_1d.get("rsi")} | EMA trend: {ind_1d.get("ema_trend")} | ATR%: {ind_1d.get("atr_pct")}%

## INSTITUTIONAL FLOW
{build_institutional_context(intel or {}, symbol)}

## RELATIVE STRENGTH
{rs_context or "No RS data."}

## CATALYST STACK
{catalyst_context or "No catalyst data."}

## INSIDER FLOW (Form 4)
{insider_context or "No insider data."}

## OPTIONS FLOW
{options_context or "No options flow data."}

## SHORT INTEREST / SQUEEZE POTENTIAL
{short_context or "No short interest data."}

## RECENT NEWS
{chr(10).join(f"- {n.get('headline','')}" for n in news[:4]) or "None."}

## YOUR RISK CHECKLIST — be brutal
Ask yourself each of these:
1. Is this trade LATE? (has the move already happened? RSI >72 for longs?)
2. Is this CROWDED? (if institutional intel shows everyone already long, who's left to buy?)
3. Is the STOP TOO TIGHT? (stop within 1 bar's noise range?)
4. Is there a BINARY EVENT risk? (earnings, FOMC, CPI within 24h?)
5. Is this COUNTER-TREND vs the daily structure?
6. Is volume BELOW average? (fakeout risk)
7. Is the R/R actually realistic, or is the target at major resistance?
8. Is this a CORRELATED CROWDED TRADE? (same direction as obvious consensus?)
9. Does the stop make structural sense, or is it arbitrary?
10. Is there a simpler explanation: is this just noise?

Return ONLY valid JSON:
{{
  "approved": true | false,
  "veto_reason": "one clear sentence if vetoed, empty string if approved",
  "risk_flags": ["flag1", "flag2"],
  "adjusted_confidence": 0.0-1.0,
  "officer_note": "one sentence summary of your assessment"
}}

Approve if: stop is structurally sound, trade is not late, R/R is realistic, no binary events, volume confirms.
Veto if: ANY of checklist items 1-4 are true, or if 3+ of items 5-10 are true."""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        approved = result.get("approved", True)
        reason   = result.get("veto_reason", "")
        return approved, reason
    except Exception:
        return True, ""   # fail open — don't block on parse error

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def calc_qty(
    portfolio_value: float,
    entry: float,
    stop_loss: float,
    atr: Optional[float] = None,
    size_multiplier: float = 1.0,
) -> int:
    risk_dollars   = portfolio_value * RISK_PER_TRADE
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        return 0
    shares     = int(risk_dollars / risk_per_share)
    max_shares = int((portfolio_value * 0.05) / entry)
    base       = max(1, min(shares, max_shares))
    # Regime-adjusted: panic shrinks to 25%, trend day grows to 150%, clamp both ends
    mult       = max(0.25, min(1.5, size_multiplier))
    return max(1, min(int(base * mult), max_shares))

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
async def execute_signal(signal: dict, portfolio_value: float, open_positions_list: Optional[list] = None) -> Optional[dict]:
    if signal.get("signal") == "hold":
        return None
    min_confidence = float(signal.get("min_confidence_required", MIN_CONFIDENCE))
    if signal.get("confidence", 0) < min_confidence:
        return None
    if signal.get("risk_reward_ratio", 0) < MIN_RR:
        return None

    symbol = signal["symbol"]
    side   = signal["signal"]
    entry  = signal.get("entry_price")
    stop   = signal.get("stop_loss")
    target = signal.get("take_profit")

    if not entry or not stop or not target:
        return None

    # --- Risk engine validation ---
    positions_for_risk = open_positions_list or []
    win_rate_frac = bot_state["win_rate"] / 100.0 if bot_state["win_rate"] > 0 else 0.5

    approved, rejection_reasons = validate_trade(
        symbol=symbol,
        side="long" if side == "buy" else "short",
        entry=entry,
        stop=stop,
        target=target,
        portfolio_value=portfolio_value,
        open_positions=positions_for_risk,
        win_rate=win_rate_frac,
    )

    if not approved:
        reasons_str = " | ".join(rejection_reasons)
        await log_bot(
            f"[{symbol}] REJECTED by risk engine: {reasons_str}",
            "warning",
        )
        return None

    # --- Kelly-adjusted position sizing ---
    kelly_frac = kelly_fraction(
        win_rate=win_rate_frac,
        avg_win_r=2.5,
        avg_loss_r=1.0,
    )

    qty = calc_qty(portfolio_value, entry, stop)
    # Scale qty by Kelly fraction relative to default RISK_PER_TRADE, then
    # re-apply the portfolio-value cap so Kelly can reduce risk or increase
    # conviction without bypassing max position exposure.
    kelly_scale = kelly_frac / RISK_PER_TRADE
    qty = max(1, int(qty * kelly_scale))
    regime_size_multiplier = float(signal.get("regime_size_multiplier", 1.0) or 1.0)
    qty = max(1, int(qty * max(0.25, min(1.25, regime_size_multiplier))))
    max_shares = int((portfolio_value * 0.05) / entry)
    if max_shares <= 0:
        return None
    qty = max(1, min(qty, max_shares))

    if qty <= 0:
        return None

    # --- Spread / slippage guard (live market check before order placement) ---
    spread_ok, spread_reason = await check_spread_slippage(symbol, side, entry, stop)
    if not spread_ok:
        await log_bot(f"[{symbol}] REJECTED by spread check: {spread_reason}", "warning")
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

    db = require_supabase()
    db.table("trades").insert({
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
        db.table("signals").update({"executed": True}).eq("id", signal_id).execute()

    bot_state["trades_today"] += 1
    return order

# ---------------------------------------------------------------------------
# Position monitor — trailing stops + P&L sync
# ---------------------------------------------------------------------------
def _is_eod_exit_window() -> bool:
    """Return True inside the last 15 minutes of the regular NYSE session."""
    now_et = datetime.now(timezone.utc).astimezone(EASTERN_TZ)
    if now_et.weekday() >= 5:
        return False
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    eod_gate = market_close - timedelta(minutes=15)
    return eod_gate <= now_et < market_close


async def monitor_positions():
    """Run every MONITOR_INTERVAL seconds. Active position management:
    trailing stops, partial profit takes, time-based exits."""
    try:
        positions_resp = await alpaca_get("/v2/positions")
        if not isinstance(positions_resp, list) or not positions_resp:
            return

        open_orders = await alpaca_get("/v2/orders?status=open&limit=200")
        if not isinstance(open_orders, list):
            open_orders = []

        eod = _is_eod_exit_window()

        for pos in positions_resp:
            symbol  = pos["symbol"]
            qty     = abs(int(float(pos["qty"])))
            side    = pos["side"]          # "long" or "short"
            entry   = float(pos["avg_entry_price"])
            current = float(pos["current_price"])
            unreal  = float(pos["unrealized_pl"])

            # Fetch original trade record for stop/target
            db = require_supabase()
            res = (
                db.table("trades")
                .select("stop_loss, exit_price, id, entry_at, created_at")
                .eq("symbol", symbol)
                .eq("status", "open")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not res.data:
                continue

            trade       = res.data[0]
            trade_id    = trade["id"]
            orig_stop   = trade.get("stop_loss")
            orig_target = trade.get("exit_price")
            entry_at_str = trade.get("entry_at") or trade.get("created_at", "")

            if not orig_stop:
                continue

            initial_risk = abs(entry - orig_stop)
            if initial_risk <= 0:
                continue

            # ── Time-based exit: force-close at end of session ──────────────
            hours_held = 0.0
            if entry_at_str:
                try:
                    entry_dt = datetime.fromisoformat(entry_at_str.replace("Z", "+00:00"))
                    hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                except Exception:
                    pass

            # EOD forced close — don't hold overnight without a deliberate swing setup
            if eod and hours_held < 24:
                await log_bot(f"[{symbol}] EOD forced close after {hours_held:.1f}h held", "warning")
                await _close_full_position(symbol)
                continue

            # Max hold: 72 hours (3 calendar days) regardless of outcome
            if hours_held > 72:
                await log_bot(f"[{symbol}] Max hold time (72h) reached — closing", "warning")
                await _close_full_position(symbol)
                continue

            # ── R-multiple tracking ──────────────────────────────────────────
            if side == "long":
                gain = current - entry
                rr   = gain / initial_risk
            else:
                gain = entry - current
                rr   = gain / initial_risk

            # ── Partial profit take at 1.5R (50% of position) ───────────────
            if rr >= 1.5 and trade_id not in _partial_taken and qty >= 2:
                partial_qty = qty // 2
                remaining_qty = qty - partial_qty
                close_side  = "sell" if side == "long" else "buy"
                try:
                    await _cancel_symbol_orders(symbol, open_orders)
                    await alpaca_post("/v2/orders", {
                        "symbol":        symbol,
                        "qty":           str(partial_qty),
                        "side":          close_side,
                        "type":          "market",
                        "time_in_force": "day",
                    })
                    _partial_taken.add(trade_id)
                    await log_bot(
                        f"[{symbol}] 💰 Partial profit: closed {partial_qty} of {qty} shares at {rr:.2f}R "
                        f"(${current:.2f}, gain=${gain*partial_qty:.0f})",
                        "info",
                    )
                    qty = remaining_qty
                    open_orders = []
                except Exception as exc:
                    await log_bot(f"[{symbol}] Partial close failed: {exc}", "error")

            # ── Trailing stop progression ────────────────────────────────────
            if side == "long":
                new_stop = None
                if rr >= 2.5:
                    new_stop = entry + initial_risk * 1.5   # lock in 1.5R
                elif rr >= 2.0:
                    new_stop = entry + initial_risk          # lock in 1R
                elif rr >= 1.5:
                    new_stop = entry + 0.02                  # protect runner after partial
                elif rr >= 1.0:
                    new_stop = entry + 0.02                  # break-even

                if new_stop and new_stop > orig_stop:
                    await _replace_stop(symbol, qty, "sell", new_stop, open_orders, orig_stop)

            elif side == "short":
                new_stop = None
                if rr >= 2.5:
                    new_stop = entry - initial_risk * 1.5
                elif rr >= 2.0:
                    new_stop = entry - initial_risk
                elif rr >= 1.5:
                    new_stop = entry - 0.02
                elif rr >= 1.0:
                    new_stop = entry - 0.02

                if new_stop and new_stop < orig_stop:
                    await _replace_stop(symbol, qty, "buy", new_stop, open_orders, orig_stop)

            # Sync live P&L to Supabase
            db.table("trades").update({"pnl": round(unreal, 2)}).eq("id", trade_id).execute()

        bot_state["last_monitor_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        await log_bot(f"Position monitor error: {e}", "error")


async def _close_full_position(symbol: str) -> None:
    """Market-close an entire open position via Alpaca."""
    try:
        open_orders = await alpaca_get("/v2/orders?status=open&limit=200")
        await _cancel_symbol_orders(symbol, open_orders if isinstance(open_orders, list) else [])
        await alpaca_delete(f"/v2/positions/{symbol}")
    except Exception as exc:
        await log_bot(f"[{symbol}] Full position close failed: {exc}", "error")


async def _cancel_symbol_orders(symbol: str, open_orders: list) -> None:
    """Cancel open orders for a symbol before manual partial/full exits."""
    for order in open_orders:
        if order.get("symbol") != symbol:
            continue
        try:
            await alpaca_delete(f"/v2/orders/{order['id']}")
        except Exception:
            pass


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
        require_supabase().table("trades").update({"stop_loss": round(new_stop, 2)}).eq("symbol", symbol).eq("status", "open").execute()
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
            require_supabase().table("trades")
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
        require_supabase().table("portfolio").upsert({
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
        if supabase is None:
            return
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
    intel: dict | None = None,
    memory: dict | None = None,
    rs_intel: dict | None = None,
    insider_intel: dict | None = None,
    options_flow_intel: dict | None = None,
    short_intel: dict | None = None,
    earnings_catalyst: bool = False,
    calendar_context: str = "",
    scan_id: str = "",
    learning_brief: str = "",
) -> Optional[dict]:
    try:
        # Event: scan started
        await write_scan_event(scan_id, symbol, "started")

        bars_5m, bars_1h, bars_1d = await asyncio.gather(
            fetch_bars(symbol, "5Min",  60),
            fetch_bars(symbol, "1Hour", 60),
            fetch_bars(symbol, "1Day",  60),
        )

        if bars_5m.empty or bars_1h.empty:
            await write_scan_event(scan_id, symbol, "error",
                rejection_reason="No bar data returned from Alpaca")
            return None

        ind_5m = compute_indicators(bars_5m)
        ind_1h = compute_indicators(bars_1h)
        ind_1d = compute_indicators(bars_1d)

        if not ind_5m or "error" in ind_5m:
            return None

        news = await fetch_news(symbol)
        setup_type, setup_confidence = classify_setup(
            ind_5m,
            ind_1h,
            ind_1d,
            news,
            earnings_catalyst=earnings_catalyst,
        )
        advanced_regime = regime.get("advanced_regime") or regime.get("regime", "unknown")
        setup_quality   = score_setup_quality(setup_type, ind_1h, advanced_regime)
        memory_context      = build_memory_prompt_section(memory or {}, symbol)
        rs_context          = build_rs_prompt_section(rs_intel or {}, symbol)
        insider_context     = build_insider_prompt_section(insider_intel or {}, symbol)
        options_context     = build_options_flow_prompt_section(options_flow_intel or {}, symbol)
        short_context       = build_short_interest_prompt_section(short_intel or {}, symbol)

        # Catalyst stack — unified evidence score
        # Merge 13F institutional + Form 4 insider into one dict for catalyst scoring
        combined_institutional = dict(intel or {})
        if insider_intel and symbol in insider_intel:
            ins = insider_intel[symbol]
            if hasattr(ins, "as_dict"):
                combined_institutional.setdefault(f"_insider_{symbol}", ins.as_dict())

        catalyst = build_catalyst_score(
            symbol=symbol,
            ind_1h=ind_1h,
            ind_1d=ind_1d,
            news=news,
            earnings_intel={"days_to_earnings": 2} if earnings_catalyst else {"days_to_earnings": 99},
            institutional_intel=combined_institutional,
            rs_intel=rs_intel or {},
            setup_type=setup_type,
            regime=advanced_regime,
        )
        catalyst_context = build_catalyst_prompt_section(catalyst)

        signal = await analyze_with_claude(
            symbol, ind_5m, ind_1h, ind_1d,
            news, regime, portfolio_value, open_positions,
            intel=intel,
            memory_context=memory_context,
            setup_type=setup_type,
            setup_quality=setup_quality,
            setup_confidence=setup_confidence,
            calendar_context=calendar_context,
            learning_brief=learning_brief,
            options_context=options_context,
            short_context=short_context,
            rs_context=rs_context,
            insider_context=insider_context,
        )

        # Step 1: catalyst stack adjusts confidence
        raw_confidence = float(signal.get("confidence", 0) or 0)
        catalyst_conf  = catalyst_confidence_boost(catalyst, raw_confidence)
        signal["catalyst_score"] = catalyst.total_score
        signal["catalyst_fired"] = catalyst.fired_catalysts

        # Step 2: historical memory calibrates further
        try:
            memory_accuracy = await get_setup_historical_accuracy(
                require_supabase(),
                symbol=symbol,
                regime=advanced_regime,
                setup_type=setup_type,
                confidence=catalyst_conf,
            )
            signal["confidence"] = memory_accuracy.get("calibrated_confidence", catalyst_conf)
            signal["memory_adjustment"] = memory_accuracy
        except Exception:
            signal["confidence"] = catalyst_conf
            signal["memory_adjustment"] = {"memory_note": "Memory calibration unavailable."}

        regime_rules = regime.get("regime_rules") or trading_rules_for_regime(advanced_regime)
        min_confidence_required = max(
            MIN_CONFIDENCE,
            float(regime_rules.get("confidence_threshold", MIN_CONFIDENCE)),
        )

        # Step 3: RS score boost on top of everything
        rs_boost = rs_score_boost(rs_intel or {}, symbol, signal.get("signal", "hold"))
        signal["confidence"] = min(0.98, signal["confidence"] + rs_boost)
        signal["rs_boost"] = rs_boost

        # Step 3.5: Insider flow adjustment
        ins_boost = insider_confidence_boost(insider_intel or {}, symbol, signal.get("signal", "hold"))
        signal["confidence"] = min(0.98, signal["confidence"] + ins_boost)
        signal["insider_boost"] = ins_boost

        # Step 3.6: Options flow adjustment
        opt_boost = options_flow_confidence_boost(options_flow_intel or {}, symbol, signal.get("signal", "hold"))
        signal["confidence"] = min(0.98, signal["confidence"] + opt_boost)
        signal["options_flow_boost"] = opt_boost

        # Step 3.7: Short interest squeeze adjustment
        si_boost = short_interest_confidence_boost(short_intel or {}, symbol, signal.get("signal", "hold"))
        signal["confidence"] = min(0.98, signal["confidence"] + si_boost)
        signal["short_interest_boost"] = si_boost
        signal["_score"] = composite_score(signal, ind_5m, ind_1h, intel) + rs_boost

        # Step 4: AI Risk Officer veto check (only for actionable signals)
        signal["risk_officer_approved"] = True
        signal["risk_officer_note"]     = ""
        if signal.get("signal") in ("buy", "sell") and signal.get("confidence", 0) >= MIN_CONFIDENCE:
            approved, veto_reason = await ai_risk_officer(
                symbol=symbol,
                trader_signal=signal,
                ind_1h=ind_1h,
                ind_1d=ind_1d,
                news=news,
                market_regime=regime,
                intel=intel,
                rs_context=rs_context,
                catalyst_context=catalyst_context,
                insider_context=insider_context,
                options_context=options_context,
                short_context=short_context,
            )
            signal["risk_officer_approved"] = approved
            signal["risk_officer_note"]     = veto_reason
            if not approved:
                await log_bot(f"[{symbol}] 🛑 Risk Officer VETO: {veto_reason}", "warning")
                signal["signal"] = "hold"
                await write_scan_event(
                    scan_id, symbol, "rejected",
                    action="veto",
                    confidence=signal.get("confidence"),
                    setup_type=setup_type,
                    setup_quality=setup_quality,
                    catalyst_score=catalyst.total_score,
                    rs_signal=(rs_intel or {}).get(symbol, {}).get("rs_signal"),
                    risk_status="vetoed",
                    rejection_reason=veto_reason,
                    payload={"trader_reasoning": signal.get("reasoning", "")},
                )

        # Event: analyzed — write the final decision for every symbol
        final_action = signal.get("signal", "hold")
        is_actionable = final_action in ("buy", "sell")
        await write_scan_event(
            scan_id, symbol,
            "accepted" if is_actionable else "analyzed",
            action=final_action,
            confidence=signal.get("confidence"),
            composite_score=signal.get("_score"),
            setup_type=setup_type,
            setup_quality=setup_quality,
            catalyst_score=catalyst.total_score,
            rs_signal=(rs_intel or {}).get(symbol, {}).get("rs_signal"),
            risk_status="approved" if signal.get("risk_officer_approved", True) else "rejected",
            payload={
                "reasoning":       signal.get("reasoning", ""),
                "criteria_met":    signal.get("criteria_met"),
                "entry_price":     signal.get("entry_price"),
                "stop_loss":       signal.get("stop_loss"),
                "take_profit":     signal.get("take_profit"),
                "risk_reward_ratio": signal.get("risk_reward_ratio"),
                "composite_score": signal.get("_score"),
                "catalyst_note":   catalyst.catalyst_note,
                "catalyst": {
                    "total_score":    catalyst.total_score,
                    "components":     catalyst.components,
                    "fired":          catalyst.fired_catalysts,
                    "dominant":       catalyst.dominant_catalyst,
                },
                "rs_note":         (rs_intel or {}).get(symbol, {}).get("rs_note", ""),
                "relative_strength": rs_intel.get(symbol) if rs_intel else None,
                "memory_note":     signal.get("memory_adjustment", {}).get("memory_note", ""),
                "memory_adjustment": signal.get("memory_adjustment"),
                "rs_boost":        signal.get("rs_boost"),
                "insider_boost":   signal.get("insider_boost"),
                "short_interest_boost": signal.get("short_interest_boost"),
                "insider_context": insider_context,
                "insider_flow": (
                    insider_intel[symbol].as_dict()
                    if insider_intel and symbol in insider_intel
                    else None
                ),
                "options_flow_boost": signal.get("options_flow_boost"),
                "options_flow": (
                    options_flow_intel[symbol].as_dict()
                    if options_flow_intel and symbol in options_flow_intel
                    else None
                ),
                "short_interest": (
                    short_intel[symbol].as_dict()
                    if short_intel and symbol in short_intel
                    else None
                ),
                "risk_officer": {
                    "approved": signal.get("risk_officer_approved", True),
                    "note":     signal.get("risk_officer_note", ""),
                },
                "setup": {
                    "type": setup_type,
                    "classification_confidence": setup_confidence,
                    "quality": setup_quality,
                    "min_confidence_required": min_confidence_required,
                },
                "trend":           mtf_trend(ind_5m, ind_1h, ind_1d),
                "regime":          advanced_regime,
            },
        )

        signal["symbol"]     = symbol
        signal["created_at"] = datetime.now(timezone.utc).isoformat()
        signal["strategy"]   = "ascend_elite_v3"
        signal["strength"]   = signal.get("confidence", 0)
        signal["setup_type"] = setup_type
        signal["setup_quality"] = setup_quality
        signal["setup_confidence"] = setup_confidence
        signal["min_confidence_required"] = min_confidence_required
        signal["catalyst_score"] = catalyst.total_score
        signal["rs_signal"] = (rs_intel or {}).get(symbol, {}).get("rs_signal")

        db = require_supabase()
        signal_insert = db.table("signals").insert({
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
                "regime": advanced_regime,
                "5m":    ind_5m,
                "1h":    ind_1h,
                "1d":    ind_1d,
                "trend": mtf_trend(ind_5m, ind_1h, ind_1d),
                "confidence_raw": raw_confidence,
                "confidence_after_catalyst": catalyst_conf,
                "rs_boost": signal.get("rs_boost"),
                "insider_boost": signal.get("insider_boost"),
                "options_flow_boost": signal.get("options_flow_boost"),
                "short_interest_boost": signal.get("short_interest_boost"),
                "setup": {
                    "type": setup_type,
                    "classification_confidence": setup_confidence,
                    "quality": setup_quality,
                    "min_confidence_required": min_confidence_required,
                    "memory_adjustment": signal.get("memory_adjustment"),
                },
                "catalyst": {
                    "total_score":    catalyst.total_score,
                    "components":     catalyst.components,
                    "fired":          catalyst.fired_catalysts,
                    "dominant":       catalyst.dominant_catalyst,
                },
                "relative_strength": rs_intel.get(symbol) if rs_intel else None,
                "insider_flow": (
                    insider_intel[symbol].as_dict()
                    if insider_intel and symbol in insider_intel
                    else None
                ),
                "options_flow": (
                    options_flow_intel[symbol].as_dict()
                    if options_flow_intel and symbol in options_flow_intel
                    else None
                ),
                "short_interest": (
                    short_intel[symbol].as_dict()
                    if short_intel and symbol in short_intel
                    else None
                ),
                "risk_officer": {
                    "approved": signal.get("risk_officer_approved", True),
                    "note":     signal.get("risk_officer_note", ""),
                },
            },
            "created_at": signal["created_at"],
            "earnings_catalyst": earnings_catalyst,
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
            f"setup={setup_type} q={setup_quality:.2f} | "
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
        scan_id = f"scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{n}"
        await log_bot(f"▶ Scan #{n} | {len(WATCHLIST)} symbols | id={scan_id}", "info")

        trade_allowed, no_trade_reason = should_trade_now()
        calendar_context = get_calendar_context()
        bot_state["no_trade_active"] = not trade_allowed
        bot_state["no_trade_reason"] = no_trade_reason or None

        if not trade_allowed:
            await log_bot(f"No-trade gate active: {no_trade_reason}", "warning")

        regime, account = await asyncio.gather(
            get_market_regime(),
            alpaca_get("/v2/account"),
        )
        regime_rules = regime.get("regime_rules") or trading_rules_for_regime(
            regime.get("advanced_regime", "unknown")
        )

        # Circuit breaker check
        if await check_circuit_breaker(account):
            await log_bot("Circuit breaker active — skipping trade execution this scan", "warning")

        portfolio_value = float(account.get("portfolio_value", 100_000))
        positions_resp  = await alpaca_get("/v2/positions")
        open_positions_list = positions_resp if isinstance(positions_resp, list) else []
        open_positions  = len(open_positions_list)

        await log_bot(
            f"Regime: {regime.get('regime','?').upper()} | "
            f"advanced={regime.get('advanced_regime','unknown')} "
            f"({float(regime.get('regime_confidence', 0) or 0):.0%}) | "
            f"${portfolio_value:,.0f} | {open_positions}/{MAX_POSITIONS} positions open",
            "info",
        )

        # --- Earnings scan: find symbols with earnings in next 1–5 days ---
        earnings_symbols: set[str] = set()
        try:
            pre_earnings = await get_pre_earnings_opportunities(
                symbols=WATCHLIST,
                alpaca_headers=ALPACA_HEADERS,
                min_days=1,
                max_days=5,
            )
            if pre_earnings:
                for opp in pre_earnings:
                    sym = opp["symbol"]
                    earnings_symbols.add(sym)
                    await log_bot(
                        f"[EARNINGS] {sym} reports in {opp['days_until']} day(s) "
                        f"({opp['earnings_date']} via {opp['source']}) — prioritising",
                        "info",
                    )
        except Exception as exc:
            await log_bot(f"Earnings scan error: {exc}", "warning")

        # Build scan order: earnings symbols first, then the rest
        earnings_first    = [s for s in WATCHLIST if s in earnings_symbols]
        non_earnings      = [s for s in WATCHLIST if s not in earnings_symbols]
        ordered_watchlist = earnings_first + non_earnings

        # Fetch institutional intel (cached — only hits SEC once per 24h)
        intel: dict = {}
        try:
            intel = await get_cached_intel(WATCHLIST)
            accumulating = [s for s, v in intel.items() if v.signal == "accumulating"]
            distributing = [s for s, v in intel.items() if v.signal == "distributing"]
            if accumulating:
                await log_bot(f"Smart money ACCUMULATING: {', '.join(accumulating)}", "info")
            if distributing:
                await log_bot(f"Smart money DISTRIBUTING: {', '.join(distributing)}", "info")
        except Exception as e:
            await log_bot(f"Institutional intel error: {e}", "warning")

        # Fetch signal memory once per scan so Claude gets empirical feedback.
        memory: dict = {}
        try:
            memory = await build_memory_from_outcomes(require_supabase())
        except Exception as e:
            await log_bot(f"Signal memory unavailable: {e}", "warning")

        # Fetch relative strength intel once per scan (cached inside module)
        rs_intel: dict = {}
        try:
            rs_intel = await get_relative_strength_intel(WATCHLIST, ALPACA_HEADERS, ALPACA_DATA_URL)
            leaders = [s for s, v in rs_intel.items() if v.get("rs_signal") == "leader"]
            laggards = [s for s, v in rs_intel.items() if v.get("rs_signal") == "laggard"]
            if leaders:
                await log_bot(f"RS Leaders: {', '.join(leaders[:5])}", "info")
            if laggards:
                await log_bot(f"RS Laggards: {', '.join(laggards[:5])}", "info")
        except Exception as e:
            await log_bot(f"RS intel error: {e}", "warning")

        # Fetch Form 4 insider buying intel (cached 4h — SEC rate-limited)
        insider_intel_map: dict = {}
        try:
            insider_raw = await get_insider_intel(WATCHLIST)
            insider_intel_map = insider_raw
            buyers = [s for s, v in insider_intel_map.items() if v.signal in ("buy", "strong_buy")]
            if buyers:
                await log_bot(f"Insider buyers: {', '.join(buyers[:5])}", "info")
        except Exception as e:
            await log_bot(f"Insider flow error: {e}", "warning")

        # Fetch options flow intel (cached 15 min)
        options_flow_map: dict = {}
        try:
            options_flow_map = await get_options_flow_intel(WATCHLIST, ALPACA_HEADERS, ALPACA_DATA_URL)
            bullish_flow = [s for s, v in options_flow_map.items() if v.flow_signal == "bullish" and v.conviction_score >= 0.4]
            bearish_flow = [s for s, v in options_flow_map.items() if v.flow_signal == "bearish" and v.conviction_score >= 0.4]
            if bullish_flow:
                await log_bot(f"Unusual CALL flow: {', '.join(bullish_flow[:5])}", "info")
            if bearish_flow:
                await log_bot(f"Unusual PUT flow: {', '.join(bearish_flow[:5])}", "info")
        except Exception as e:
            await log_bot(f"Options flow error: {e}", "warning")

        # Fetch short interest intel (cached 4h — Finviz)
        short_intel_map: dict = {}
        try:
            short_intel_map = await get_short_interest_intel(WATCHLIST)
            squeeze_plays = [s for s, v in short_intel_map.items() if v.squeeze_signal in ("extreme", "high")]
            if squeeze_plays:
                await log_bot(f"Squeeze candidates: {', '.join(squeeze_plays[:5])}", "info")
        except Exception as e:
            await log_bot(f"Short interest error: {e}", "warning")

        # Build the learning brief from memory (injected into every Claude prompt)
        # Augment with signal attribution insights so Claude knows which signals are predictive
        base_brief = get_learning_brief()
        try:
            attr_rows = await load_attribution_rows(require_supabase(), limit=300)
            attr_insights = get_component_stats(attr_rows)
            attribution_section = build_attribution_prompt_section(attr_insights)
            learning_brief = f"{base_brief}\n\n{attribution_section}" if base_brief else attribution_section
        except Exception:
            learning_brief = base_brief

        # Check the weakness report — skip symbols the bot is systematically bad at
        symbols_to_avoid = get_symbols_to_avoid()
        if symbols_to_avoid:
            await log_bot(f"Memory AVOID list: {', '.join(symbols_to_avoid)}", "warning")

        # Scan all symbols in batches (skip systematic losers)
        scannable = [s for s in ordered_watchlist if s not in symbols_to_avoid]
        if len(scannable) < len(ordered_watchlist):
            skipped = set(ordered_watchlist) - set(scannable)
            await log_bot(f"Skipping {len(skipped)} avoid-listed symbol(s): {', '.join(skipped)}", "warning")

        all_signals: list[dict] = []
        for i in range(0, len(scannable), BATCH_SIZE):
            if not bot_state["running"]:
                return
            batch   = scannable[i : i + BATCH_SIZE]
            results = await asyncio.gather(*[
                scan_symbol(
                    sym,
                    regime,
                    portfolio_value,
                    open_positions,
                    intel=intel,
                    memory=memory,
                    earnings_catalyst=sym in earnings_symbols,
                    calendar_context=calendar_context,
                    rs_intel=rs_intel,
                    insider_intel=insider_intel_map,
                    options_flow_intel=options_flow_map,
                    short_intel=short_intel_map,
                    scan_id=scan_id,
                    learning_brief=learning_brief,
                )
                for sym in batch
            ])
            for s in results:
                if s is not None:
                    if s.get("symbol") in earnings_symbols:
                        s["earnings_catalyst"] = True
                    all_signals.append(s)
            await asyncio.sleep(1.5)

        # Filter and rank by composite score
        # Earnings-catalyst signals get a small score boost to keep them near front
        def _sort_key(sig: dict) -> float:
            base = sig.get("_score", 0)
            return base + (0.05 if sig.get("earnings_catalyst") else 0.0)

        gated_signals: list[dict] = []
        gate_rejections = 0
        for signal in all_signals:
            if signal.get("signal") not in ("buy", "sell"):
                continue
            approved, gate_reason = apply_portfolio_regime_gate(signal, regime, regime_rules)
            signal["portfolio_gate"]["approved"] = approved
            signal["portfolio_gate"]["reason"] = gate_reason
            if approved:
                gated_signals.append(signal)
            else:
                gate_rejections += 1
                await write_scan_event(
                    scan_id, signal["symbol"], "rejected",
                    action="risk_fail",
                    confidence=signal.get("confidence"),
                    composite_score=signal.get("_score"),
                    setup_type=signal.get("setup_type"),
                    setup_quality=signal.get("setup_quality"),
                    catalyst_score=signal.get("catalyst_score"),
                    rs_signal=signal.get("rs_signal"),
                    risk_status="portfolio_gate",
                    rejection_reason=gate_reason,
                    payload={
                        "portfolio_gate": signal.get("portfolio_gate"),
                        "regime": regime,
                        "regime_rules": regime_rules,
                        "reasoning": signal.get("reasoning", ""),
                    },
                )

        actionable = sorted(
            [
                s for s in gated_signals
                if s.get("signal") in ("buy", "sell")
                and s.get("confidence", 0) >= s.get("min_confidence_required", MIN_CONFIDENCE)
                and s.get("risk_reward_ratio", 0) >= MIN_RR
            ],
            key=_sort_key,
            reverse=True,
        )

        max_positions_allowed = min(MAX_POSITIONS, int(regime_rules.get("max_positions", MAX_POSITIONS)))
        slots = max(0, max_positions_allowed - open_positions)
        await log_bot(
            f"{len(actionable)} high-conviction signals | {slots} slots | "
            f"portfolio_gate_rejections={gate_rejections} | "
            f"circuit_breaker={bot_state['circuit_breaker_active']} | "
            f"no_trade={not trade_allowed} | "
            f"earnings_catalysts={len(earnings_symbols)}",
            "info",
        )

        no_trade_mode = bot_state.get("no_trade_mode", "strict")
        execution_blocked = (
            bot_state["circuit_breaker_active"]
            or no_trade_mode == "monitor_only"
            or (not trade_allowed and no_trade_mode == "strict")
        )

        if not execution_blocked:
            for signal in actionable[:slots]:
                if not bot_state["running"]:
                    break
                try:
                    order = await execute_signal(
                        signal, portfolio_value, open_positions_list
                    )
                    if order:
                        earnings_tag = " [EARNINGS]" if signal.get("earnings_catalyst") else ""
                        await log_bot(
                            f"✅ TRADE{earnings_tag}: {signal['signal'].upper()} {signal['symbol']} | "
                            f"conf={signal['confidence']:.0%} | "
                            f"R/R={signal.get('risk_reward_ratio',0):.1f} | "
                            f"entry=${signal['entry_price']} | "
                            f"stop=${signal['stop_loss']} | "
                            f"target=${signal['take_profit']}",
                            "info",
                        )
                        await write_scan_event(
                            scan_id, signal["symbol"], "ordered",
                            action=signal["signal"],
                            confidence=signal.get("confidence"),
                            composite_score=signal.get("_score"),
                            setup_type=signal.get("setup_type"),
                            setup_quality=signal.get("setup_quality"),
                            catalyst_score=signal.get("catalyst_score"),
                            risk_status="approved",
                            payload={
                                "entry_price": signal.get("entry_price"),
                                "stop_loss":   signal.get("stop_loss"),
                                "take_profit": signal.get("take_profit"),
                                "rr":          signal.get("risk_reward_ratio"),
                                "portfolio_gate": signal.get("portfolio_gate"),
                                "regime_size_multiplier": signal.get("regime_size_multiplier"),
                                "order_id":    order.get("id"),
                            },
                        )
                except Exception as e:
                    await log_bot(f"Order failed [{signal['symbol']}]: {e}", "error")
                    await write_scan_event(
                        scan_id, signal["symbol"], "error",
                        rejection_reason=str(e),
                        payload={"error": str(e)},
                    )
        elif actionable:
            reason = (
                "monitor_only mode" if no_trade_mode == "monitor_only"
                else "circuit breaker active" if bot_state["circuit_breaker_active"]
                else no_trade_reason or "no-trade gate"
            )
            await log_bot(
                f"Execution skipped for {len(actionable)} signal(s): {reason}",
                "warning",
            )

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


@app.post("/config")
async def update_config(body: dict):
    """
    Update runtime bot configuration.

    Accepted keys:
      no_trade_mode: "strict" | "balanced" | "monitor_only"
    """
    allowed = {"no_trade_mode"}
    valid_modes = {"strict", "balanced", "monitor_only"}
    updated = {}

    if "no_trade_mode" in body:
        mode = body["no_trade_mode"]
        if mode not in valid_modes:
            raise HTTPException(400, f"no_trade_mode must be one of {valid_modes}")
        bot_state["no_trade_mode"] = mode
        updated["no_trade_mode"] = mode
        await log_bot(f"Config updated: no_trade_mode={mode}", "info")

    unknown = set(body.keys()) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown config keys: {unknown}")

    return {"status": "updated", "config": updated}


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
        require_supabase().table("signal_outcomes")
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
        result = await run_research_backtest(config, require_supabase())
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
        require_supabase().table("backtest_runs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


@app.get("/backtests/{run_id}/trades")
async def list_backtest_trades(run_id: str, limit: int = 100):
    res = (
        require_supabase().table("backtest_trades")
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


@app.post("/options/enable")
async def enable_options_mode():
    """
    Enable options research mode. This keeps options visible to the system
    while equity execution remains the only live order path until option
    exits and monitoring are fully implemented.
    """
    bot_state["options_mode"] = True
    await log_bot("Options research mode ENABLED — options are advisory until execution monitoring is added", "info")
    return {"options_mode": True, "status": "enabled"}


@app.post("/options/disable")
async def disable_options_mode():
    """Disable options research mode."""
    bot_state["options_mode"] = False
    await log_bot("Options research mode DISABLED", "info")
    return {"options_mode": False, "status": "disabled"}


@app.get("/options/status")
async def options_status():
    """Return current options mode state."""
    return {
        "options_mode": bot_state["options_mode"],
        "note": "Advisory only until options exit monitoring and paper validation are complete.",
    }
