"""
Ascend Research Lab backtester.

This module replays historical bars through a deterministic version of the
Ascend Elite signal stack. It is intentionally Claude-free: first prove the
market edge, then spend AI tokens on the highest-value decisions.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pandas_ta as ta
from supabase import Client


ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AMD", "META", "AMZN", "MSFT", "AAPL", "PLTR"]
DEFAULT_INITIAL_EQUITY = 100_000.0
RISK_PER_TRADE = 0.01
MAX_POSITION_PCT = 0.08
MIN_CONFIDENCE = 0.68
MIN_RR = 2.0
MAX_HOLD_BARS = 24


@dataclass
class BacktestConfig:
    symbols: list[str]
    timeframe: str
    start_at: datetime
    end_at: datetime
    initial_equity: float = DEFAULT_INITIAL_EQUITY


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def fetch_historical_bars(
    symbol: str,
    timeframe: str,
    start_at: datetime,
    end_at: datetime,
) -> pd.DataFrame:
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise RuntimeError("Alpaca API keys are not configured")

    rows: list[dict[str, Any]] = []
    page_token = None

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {
                "timeframe": timeframe,
                "start": _iso(start_at),
                "end": _iso(end_at),
                "adjustment": "raw",
                "limit": 10_000,
            }
            if page_token:
                params["page_token"] = page_token

            r = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
                headers=ALPACA_HEADERS,
                params=params,
            )
            r.raise_for_status()
            payload = r.json()
            rows.extend(payload.get("bars", []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    return df.set_index("t").sort_index()


def compute_backtest_indicators(window: pd.DataFrame) -> dict[str, Any]:
    close = window["close"]
    high = window["high"]
    low = window["low"]
    volume = window["volume"]
    price = float(close.iloc[-1])

    rsi_s = ta.rsi(close, length=14)
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    atr_s = ta.atr(high, low, close, length=14)

    ema9 = float(ta.ema(close, length=9).iloc[-1])
    ema21 = float(ta.ema(close, length=21).iloc[-1])
    ema50 = float(ta.ema(close, length=50).iloc[-1])
    vol_sma = float(volume.rolling(20).mean().iloc[-1])
    volume_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma > 0 else 1.0
    vwap = float((close * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])

    macd_hist = 0.0
    if macd_df is not None and not macd_df.empty:
        macd_hist = float(macd_df["MACDh_12_26_9"].iloc[-1])

    atr = None
    if atr_s is not None and not atr_s.isna().all():
        atr = float(atr_s.iloc[-1])

    roc5 = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100)
    rsi = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.isna().all() else 50.0
    ema_trend = "bullish" if ema9 > ema21 > ema50 else "bearish" if ema9 < ema21 < ema50 else "mixed"

    return {
        "price": price,
        "rsi": rsi,
        "macd_histogram": macd_hist,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "ema_trend": ema_trend,
        "above_vwap": price > vwap,
        "above_ema21": price > ema21,
        "atr": atr,
        "volume_ratio": volume_ratio,
        "roc5": roc5,
    }


def generate_research_signal(ind: dict[str, Any]) -> dict[str, Any]:
    price = float(ind["price"])
    atr = float(ind.get("atr") or 0)
    if atr <= 0:
        return {"signal": "hold", "confidence": 0, "criteria_met": 0}

    long_checks = {
        "trend": ind["ema_trend"] == "bullish",
        "volume": ind["volume_ratio"] >= 1.25,
        "rsi": 38 <= ind["rsi"] <= 68,
        "momentum": ind["macd_histogram"] > 0 and ind["roc5"] > 0,
        "structure": ind["above_vwap"] and ind["above_ema21"],
    }
    short_checks = {
        "trend": ind["ema_trend"] == "bearish",
        "volume": ind["volume_ratio"] >= 1.25,
        "rsi": 32 <= ind["rsi"] <= 62,
        "momentum": ind["macd_histogram"] < 0 and ind["roc5"] < 0,
        "structure": (not ind["above_vwap"]) and (not ind["above_ema21"]),
    }

    long_score = sum(long_checks.values())
    short_score = sum(short_checks.values())
    side = "buy" if long_score >= short_score else "sell"
    criteria_met = max(long_score, short_score)

    if criteria_met < 4:
        return {"signal": "hold", "confidence": criteria_met / 7, "criteria_met": criteria_met}

    confidence = min(0.95, 0.45 + criteria_met * 0.08 + min(ind["volume_ratio"], 4) * 0.025)
    stop_distance = max(atr * 1.25, price * 0.006)
    target_distance = stop_distance * 2.4

    if side == "buy":
        stop_loss = price - stop_distance
        take_profit = price + target_distance
    else:
        stop_loss = price + stop_distance
        take_profit = price - target_distance

    return {
        "signal": side,
        "confidence": confidence,
        "criteria_met": criteria_met,
        "entry_price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_reward_ratio": target_distance / stop_distance,
    }


def _simulate_trade(
    future: pd.DataFrame,
    side: str,
    entry: float,
    stop: float,
    target: float,
) -> tuple[float, datetime, str, float]:
    risk = abs(entry - stop)
    for ts, row in future.iloc[1 : MAX_HOLD_BARS + 1].iterrows():
        low = float(row["low"])
        high = float(row["high"])

        if side == "buy":
            stop_hit = low <= stop
            target_hit = high >= target
            if stop_hit and target_hit:
                return stop, ts.to_pydatetime(), "ambiguous_stop_first", -1.0
            if stop_hit:
                return stop, ts.to_pydatetime(), "stop_loss", -1.0
            if target_hit:
                return target, ts.to_pydatetime(), "take_profit", (target - entry) / risk
        else:
            stop_hit = high >= stop
            target_hit = low <= target
            if stop_hit and target_hit:
                return stop, ts.to_pydatetime(), "ambiguous_stop_first", -1.0
            if stop_hit:
                return stop, ts.to_pydatetime(), "stop_loss", -1.0
            if target_hit:
                return target, ts.to_pydatetime(), "take_profit", (entry - target) / risk

    exit_row = future.iloc[min(MAX_HOLD_BARS, len(future) - 1)]
    exit_price = float(exit_row["close"])
    exit_at = future.index[min(MAX_HOLD_BARS, len(future) - 1)].to_pydatetime()
    r_mult = ((exit_price - entry) / risk) if side == "buy" else ((entry - exit_price) / risk)
    return exit_price, exit_at, "time_exit", r_mult


def _metrics(equity_curve: list[float], r_multiples: list[float]) -> dict[str, float]:
    if len(equity_curve) < 2:
        return {
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "sharpe": 0.0,
        }

    initial = equity_curve[0]
    final = equity_curve[-1]
    peaks = np.maximum.accumulate(equity_curve)
    drawdowns = (np.array(equity_curve) - peaks) / peaks * 100
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    returns = np.diff(equity_curve) / np.array(equity_curve[:-1])

    sharpe = 0.0
    if returns.size > 1 and float(np.std(returns)) > 0:
        sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(252))

    return {
        "total_return_pct": (final - initial) / initial * 100,
        "max_drawdown_pct": float(abs(drawdowns.min())),
        "win_rate": len(wins) / len(r_multiples) * 100 if r_multiples else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else gross_win,
        "expectancy_r": float(np.mean(r_multiples)) if r_multiples else 0.0,
        "sharpe": sharpe,
    }


async def run_research_backtest(config: BacktestConfig, supabase: Client) -> dict[str, Any]:
    equity = config.initial_equity
    equity_curve = [equity]
    trades: list[dict[str, Any]] = []
    r_multiples: list[float] = []

    for symbol in config.symbols:
        bars = await fetch_historical_bars(symbol, config.timeframe, config.start_at, config.end_at)
        if bars.empty or len(bars) < 90:
            continue

        cooldown_until = 0
        for i in range(60, len(bars) - MAX_HOLD_BARS - 1):
            if i < cooldown_until:
                continue

            window = bars.iloc[: i + 1]
            ind = compute_backtest_indicators(window)
            signal = generate_research_signal(ind)
            if signal["signal"] == "hold" or signal["confidence"] < MIN_CONFIDENCE:
                continue

            side = signal["signal"]
            entry = float(signal["entry_price"])
            stop = float(signal["stop_loss"])
            target = float(signal["take_profit"])
            risk_per_share = abs(entry - stop)
            if risk_per_share <= 0 or signal["risk_reward_ratio"] < MIN_RR:
                continue

            risk_dollars = equity * RISK_PER_TRADE
            max_position_dollars = equity * MAX_POSITION_PCT
            qty = max(1, min(int(risk_dollars / risk_per_share), int(max_position_dollars / entry)))
            if qty <= 0:
                continue

            exit_price, exit_at, exit_reason, r_mult = _simulate_trade(
                bars.iloc[i:], side, entry, stop, target
            )
            pnl = (exit_price - entry) * qty if side == "buy" else (entry - exit_price) * qty
            equity += pnl
            equity_curve.append(equity)
            r_multiples.append(r_mult)
            cooldown_until = i + MAX_HOLD_BARS

            trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_at": bars.index[i].to_pydatetime().isoformat(),
                    "exit_at": exit_at.isoformat(),
                    "entry_price": round(entry, 4),
                    "exit_price": round(exit_price, 4),
                    "stop_loss": round(stop, 4),
                    "take_profit": round(target, 4),
                    "qty": qty,
                    "pnl": round(pnl, 2),
                    "r_multiple": round(r_mult, 3),
                    "confidence": round(float(signal["confidence"]), 4),
                    "criteria_met": int(signal["criteria_met"]),
                    "exit_reason": exit_reason,
                    "indicators": {
                        "rsi": round(float(ind["rsi"]), 2),
                        "volume_ratio": round(float(ind["volume_ratio"]), 2),
                        "roc5": round(float(ind["roc5"]), 3),
                        "ema_trend": ind["ema_trend"],
                    },
                }
            )

    metrics = _metrics(equity_curve, r_multiples)
    wins = len([r for r in r_multiples if r > 0])
    losses = len(r_multiples) - wins

    run_payload = {
        "strategy": "ascend_research_v1",
        "symbols": config.symbols,
        "timeframe": config.timeframe,
        "start_at": config.start_at.isoformat(),
        "end_at": config.end_at.isoformat(),
        "initial_equity": round(config.initial_equity, 2),
        "final_equity": round(equity, 2),
        "total_return_pct": round(metrics["total_return_pct"], 3),
        "max_drawdown_pct": round(metrics["max_drawdown_pct"], 3),
        "win_rate": round(metrics["win_rate"], 3),
        "profit_factor": round(metrics["profit_factor"], 3),
        "expectancy_r": round(metrics["expectancy_r"], 3),
        "sharpe": round(metrics["sharpe"], 3),
        "total_trades": len(trades),
        "winning_trades": wins,
        "losing_trades": losses,
        "config": {
            "risk_per_trade": RISK_PER_TRADE,
            "max_position_pct": MAX_POSITION_PCT,
            "min_confidence": MIN_CONFIDENCE,
            "min_rr": MIN_RR,
            "max_hold_bars": MAX_HOLD_BARS,
        },
    }

    run_res = supabase.table("backtest_runs").insert(run_payload).execute()
    run_id = run_res.data[0]["id"]

    if trades:
        rows = [{**trade, "run_id": run_id} for trade in trades[:500]]
        supabase.table("backtest_trades").insert(rows).execute()

    return {**run_payload, "id": run_id, "sample_trades": trades[:25]}


def parse_config(payload: dict[str, Any] | None = None) -> BacktestConfig:
    payload = payload or {}
    end_at = datetime.now(timezone.utc)
    start_at = end_at - timedelta(days=int(payload.get("days", 180)))

    if payload.get("start_at"):
        start_at = datetime.fromisoformat(str(payload["start_at"]).replace("Z", "+00:00"))
    if payload.get("end_at"):
        end_at = datetime.fromisoformat(str(payload["end_at"]).replace("Z", "+00:00"))

    symbols = payload.get("symbols") or DEFAULT_SYMBOLS
    if isinstance(symbols, str):
        symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    return BacktestConfig(
        symbols=[str(s).upper() for s in symbols][:24],
        timeframe=str(payload.get("timeframe", "1Hour")),
        start_at=start_at,
        end_at=end_at,
        initial_equity=float(payload.get("initial_equity", DEFAULT_INITIAL_EQUITY)),
    )
