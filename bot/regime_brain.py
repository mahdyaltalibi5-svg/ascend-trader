"""
regime_brain.py — Market regime classification engine for Ascend Trader.

Classifies the current market into one of 7 distinct regimes and provides
trading rules that govern bot behavior for each regime.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ETF symbols used for regime detection
# ---------------------------------------------------------------------------
REGIME_SYMBOLS = ["SPY", "QQQ", "SMH", "XLK", "IWM"]

VALID_REGIMES = {
    "trend_day",
    "chop_range",
    "high_vol_panic",
    "low_vol_drift",
    "risk_on",
    "risk_off",
    "sector_rotation",
    "unknown",
}


# ---------------------------------------------------------------------------
# Technical indicator helpers
# ---------------------------------------------------------------------------

def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """
    Compute the Average Directional Index (ADX) manually using pandas.

    Returns the most recent ADX value as a float, or 0.0 on insufficient data.
    """
    if len(close) < period + 1:
        return 0.0

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Directional movement
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=close.index, dtype=float)
    minus_dm = pd.Series(0.0, index=close.index, dtype=float)

    cond_up = (up_move > down_move) & (up_move > 0)
    cond_down = (down_move > up_move) & (down_move > 0)

    plus_dm[cond_up] = up_move[cond_up]
    minus_dm[cond_down] = down_move[cond_down]

    # Smoothed (Wilder's EMA-style) values
    alpha = 1.0 / period

    atr_s = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_s.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_s.replace(0, float("nan"))

    dx_num = (plus_di - minus_di).abs()
    dx_den = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * dx_num / dx_den

    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return float(adx.iloc[-1]) if not adx.empty else 0.0


def compute_bb_width(close: pd.Series, period: int = 20) -> float:
    """
    Bollinger Band width: (upper - lower) / middle.

    Returns the most recent BB width value or 0.0 on insufficient data.
    """
    if len(close) < period:
        return 0.0

    middle = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = middle + 2 * std
    lower = middle - 2 * std

    bb_width = (upper - lower) / middle.replace(0, float("nan"))
    return float(bb_width.iloc[-1]) if not bb_width.empty else 0.0


def _n_day_return(close: pd.Series, n: int = 5) -> float:
    """Return the n-bar return of the last available bar."""
    if len(close) < n + 1:
        return 0.0
    return float((close.iloc[-1] / close.iloc[-n - 1]) - 1)


def _vol_ratio(volume: pd.Series, period: int = 20) -> float:
    """Current volume bar vs rolling average."""
    if len(volume) < period + 1:
        return 1.0
    avg = float(volume.iloc[:-1].rolling(period).mean().iloc[-1])
    if avg == 0:
        return 1.0
    return float(volume.iloc[-1]) / avg


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

async def fetch_regime_bars(
    symbols: list[str],
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
) -> dict[str, pd.DataFrame]:
    """
    Fetch 1-hour OHLCV bars for the given symbols from Alpaca.

    Returns a dict mapping symbol -> DataFrame with columns
    [open, high, low, close, volume] indexed by timestamp.
    Missing or errored symbols map to an empty DataFrame.
    """
    results: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in symbols}

    async def _fetch_one(client: httpx.AsyncClient, symbol: str) -> tuple[str, pd.DataFrame]:
        url = f"{alpaca_data_url.rstrip('/')}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": "1Hour",
            "limit": 100,
            "adjustment": "raw",
            "feed": "iex",
        }
        try:
            response = await client.get(url, params=params, headers=alpaca_headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            bars = data.get("bars", [])
            if not bars:
                return symbol, pd.DataFrame()
            df = pd.DataFrame(bars)
            df = df.rename(
                columns={
                    "t": "timestamp",
                    "o": "open",
                    "h": "high",
                    "l": "low",
                    "c": "close",
                    "v": "volume",
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return symbol, df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_regime_bars: %s failed — %s", symbol, exc)
            return symbol, pd.DataFrame()

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_one(client, sym) for sym in symbols]
        pairs = await asyncio.gather(*tasks)

    for symbol, df in pairs:
        results[symbol] = df

    return results


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def classify_regime(
    spy_bars: pd.DataFrame,
    qqq_bars: pd.DataFrame,
    smh_bars: pd.DataFrame,
    xlk_bars: pd.DataFrame,
    iwm_bars: pd.DataFrame,
) -> dict[str, Any]:
    """
    Classify the current market regime from ETF bar data.

    Returns a dict with keys:
        regime, confidence, leading_sector, spy_adx,
        qqq_vs_spy_rs, smh_vs_spy_rs, vol_ratio, bb_width, regime_note
    """
    # Defaults in case we have insufficient data for a symbol
    def _safe_adx(df: pd.DataFrame) -> float:
        if df.empty or len(df) < 15:
            return 0.0
        return compute_adx(df["high"], df["low"], df["close"])

    def _safe_bbw(df: pd.DataFrame) -> float:
        if df.empty or len(df) < 20:
            return 0.0
        return compute_bb_width(df["close"])

    def _safe_ret(df: pd.DataFrame, n: int = 5) -> float:
        if df.empty:
            return 0.0
        return _n_day_return(df["close"], n)

    def _safe_vol_ratio(df: pd.DataFrame) -> float:
        if df.empty or "volume" not in df.columns:
            return 1.0
        return _vol_ratio(df["volume"])

    spy_adx = _safe_adx(spy_bars)
    spy_bbw = _safe_bbw(spy_bars)
    vol_r = _safe_vol_ratio(spy_bars)

    spy_ret5 = _safe_ret(spy_bars, 5)
    qqq_ret5 = _safe_ret(qqq_bars, 5)
    smh_ret5 = _safe_ret(smh_bars, 5)
    xlk_ret5 = _safe_ret(xlk_bars, 5)
    iwm_ret5 = _safe_ret(iwm_bars, 5)

    qqq_vs_spy = qqq_ret5 - spy_ret5
    smh_vs_spy = smh_ret5 - spy_ret5
    xlk_vs_spy = xlk_ret5 - spy_ret5
    iwm_vs_spy = iwm_ret5 - spy_ret5

    # Estimate current ATR for SPY to detect panic
    atr_expansion = 1.0
    if not spy_bars.empty and len(spy_bars) >= 22:
        close = spy_bars["close"]
        high = spy_bars["high"]
        low = spy_bars["low"]
        tr = pd.concat(
            [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
        ).max(axis=1)
        recent_atr = float(tr.iloc[-5:].mean())
        baseline_atr = float(tr.iloc[-22:-5].mean())
        atr_expansion = recent_atr / baseline_atr if baseline_atr > 0 else 1.0

    # -----------------------------------------------------------------------
    # Regime scoring — evaluate each candidate and pick the highest-scoring
    # -----------------------------------------------------------------------
    scores: dict[str, float] = {r: 0.0 for r in VALID_REGIMES - {"unknown"}}

    # ---- trend_day ---------------------------------------------------------
    if spy_adx > 25:
        scores["trend_day"] += 0.5
    if spy_adx > 30:
        scores["trend_day"] += 0.2
    if vol_r > 1.1:
        scores["trend_day"] += 0.2
    if spy_bbw > 0.06:
        scores["trend_day"] += 0.1

    # ---- chop_range --------------------------------------------------------
    if spy_adx < 20:
        scores["chop_range"] += 0.4
    if spy_bbw < 0.04:
        scores["chop_range"] += 0.4
    if vol_r < 0.9:
        scores["chop_range"] += 0.2

    # ---- high_vol_panic ----------------------------------------------------
    if atr_expansion > 2.0:
        scores["high_vol_panic"] += 0.6
    elif atr_expansion > 1.5:
        scores["high_vol_panic"] += 0.3
    if spy_bbw > 0.08:
        scores["high_vol_panic"] += 0.2
    if vol_r > 1.5:
        scores["high_vol_panic"] += 0.2

    # ---- low_vol_drift -----------------------------------------------------
    if vol_r < 0.6:
        scores["low_vol_drift"] += 0.5
    if spy_bbw < 0.03:
        scores["low_vol_drift"] += 0.3
    if spy_adx < 15:
        scores["low_vol_drift"] += 0.2

    # ---- risk_on -----------------------------------------------------------
    if qqq_vs_spy > 0.005:
        scores["risk_on"] += 0.3
    if smh_vs_spy > 0.01:
        scores["risk_on"] += 0.3
    if xlk_vs_spy > 0.005:
        scores["risk_on"] += 0.2
    if iwm_vs_spy > 0.005:
        scores["risk_on"] += 0.2

    # ---- risk_off ----------------------------------------------------------
    if qqq_vs_spy < -0.005:
        scores["risk_off"] += 0.3
    if smh_vs_spy < -0.01:
        scores["risk_off"] += 0.3
    if spy_ret5 < -0.02:
        scores["risk_off"] += 0.2
    if vol_r > 1.2:
        scores["risk_off"] += 0.2

    # ---- sector_rotation ---------------------------------------------------
    rs_values = {
        "semis": smh_vs_spy,
        "tech": xlk_vs_spy,
        "small_caps": iwm_vs_spy,
    }
    max_rs = max(rs_values.values())
    min_rs = min(rs_values.values())
    if max_rs > 0.015 and (max_rs - min_rs) > 0.02:
        scores["sector_rotation"] += 0.6
    elif max_rs > 0.008:
        scores["sector_rotation"] += 0.3

    # -----------------------------------------------------------------------
    # Pick winner
    # -----------------------------------------------------------------------
    best_regime = max(scores, key=lambda k: scores[k])
    best_score = scores[best_regime]

    # Confidence: normalize the winner score; fallback to unknown if too low
    if best_score < 0.25:
        best_regime = "unknown"
        confidence = 0.0
    else:
        confidence = min(1.0, round(best_score, 2))

    # Leading sector
    rs_map = {
        "semis": smh_vs_spy,
        "tech": xlk_vs_spy,
        "small_caps": iwm_vs_spy,
    }
    top_sector = max(rs_map, key=lambda k: rs_map[k])
    leading_sector: str
    if rs_map[top_sector] > 0.005:
        leading_sector = top_sector
    elif abs(qqq_vs_spy) < 0.003 and abs(smh_vs_spy) < 0.003:
        leading_sector = "broad"
    else:
        leading_sector = "none"

    # Human-readable note
    notes: dict[str, str] = {
        "trend_day": (
            f"ADX {spy_adx:.1f} signals strong directional trend; volume {vol_r:.2f}x average "
            f"confirms momentum participation."
        ),
        "chop_range": (
            f"ADX {spy_adx:.1f} and tight Bollinger Band width {spy_bbw:.3f} indicate "
            "range-bound chop — breakout signals likely to fail."
        ),
        "high_vol_panic": (
            f"ATR has expanded {atr_expansion:.1f}x vs baseline; wide candles and elevated "
            "volume suggest panic or news-driven dislocation."
        ),
        "low_vol_drift": (
            f"Volume at {vol_r:.2f}x average with BBW {spy_bbw:.3f} — thin liquidity environment "
            "prone to fakeouts; prefer staying flat."
        ),
        "risk_on": (
            f"QQQ outperforming SPY by {qqq_vs_spy*100:.2f}% and semis by {smh_vs_spy*100:.2f}% "
            "over 5 bars — growth and tech leadership points to risk-on regime."
        ),
        "risk_off": (
            f"QQQ lagging SPY by {abs(qqq_vs_spy)*100:.2f}% and semis by {abs(smh_vs_spy)*100:.2f}% "
            "— defensive rotation underway, reduce exposure."
        ),
        "sector_rotation": (
            f"Top relative-strength sector ({top_sector}) is outperforming SPY by "
            f"{rs_map.get(top_sector, 0)*100:.2f}% while others lag — follow the hot sector."
        ),
        "unknown": "Insufficient data or mixed signals; regime could not be determined.",
    }

    return {
        "regime": best_regime,
        "confidence": confidence,
        "leading_sector": leading_sector,
        "spy_adx": round(spy_adx, 2),
        "qqq_vs_spy_rs": round(qqq_vs_spy, 4),
        "smh_vs_spy_rs": round(smh_vs_spy, 4),
        "vol_ratio": round(vol_r, 3),
        "bb_width": round(spy_bbw, 4),
        "regime_note": notes.get(best_regime, ""),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_full_regime(
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
) -> dict[str, Any]:
    """
    Fetch all regime ETF bars concurrently and return classify_regime output.

    Falls back to {"regime": "unknown", ...} on any unhandled exception.
    """
    try:
        bars = await fetch_regime_bars(REGIME_SYMBOLS, alpaca_headers, alpaca_data_url)
        return classify_regime(
            spy_bars=bars.get("SPY", pd.DataFrame()),
            qqq_bars=bars.get("QQQ", pd.DataFrame()),
            smh_bars=bars.get("SMH", pd.DataFrame()),
            xlk_bars=bars.get("XLK", pd.DataFrame()),
            iwm_bars=bars.get("IWM", pd.DataFrame()),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("get_full_regime failed: %s", exc, exc_info=True)
        return {
            "regime": "unknown",
            "confidence": 0.0,
            "leading_sector": "none",
            "spy_adx": 0.0,
            "qqq_vs_spy_rs": 0.0,
            "smh_vs_spy_rs": 0.0,
            "vol_ratio": 1.0,
            "bb_width": 0.0,
            "regime_note": f"Regime detection failed: {exc}",
        }


# ---------------------------------------------------------------------------
# Trading rules per regime
# ---------------------------------------------------------------------------

def trading_rules_for_regime(regime: str) -> dict[str, Any]:
    """
    Return trading rules that govern bot behavior for the given regime.

    Keys:
        max_positions       — maximum concurrent open positions
        confidence_threshold — minimum signal confidence to enter a trade
        preferred_setups    — setup types to actively seek
        avoid_setups        — setup types to skip
        size_multiplier     — multiply base position size by this factor
        note                — one-sentence rationale
    """
    rules: dict[str, dict[str, Any]] = {
        "trend_day": {
            "max_positions": 6,
            "confidence_threshold": 0.55,
            "preferred_setups": ["breakout", "momentum", "trend_continuation"],
            "avoid_setups": ["mean_reversion", "fade"],
            "size_multiplier": 1.2,
            "note": "Strong directional day — lean into momentum and breakouts with slightly larger size.",
        },
        "chop_range": {
            "max_positions": 3,
            "confidence_threshold": 0.70,
            "preferred_setups": ["mean_reversion", "range_fade", "support_resistance"],
            "avoid_setups": ["breakout", "momentum", "trend_continuation"],
            "size_multiplier": 0.7,
            "note": "Choppy range — only high-conviction mean-reversion setups; breakouts will fail.",
        },
        "high_vol_panic": {
            "max_positions": 2,
            "confidence_threshold": 0.80,
            "preferred_setups": ["capitulation_reversal", "vwap_reclaim"],
            "avoid_setups": ["breakout", "momentum", "mean_reversion"],
            "size_multiplier": 0.4,
            "note": "Panic conditions — drastically reduce size and require very high conviction before entry.",
        },
        "low_vol_drift": {
            "max_positions": 2,
            "confidence_threshold": 0.75,
            "preferred_setups": ["range_fade"],
            "avoid_setups": ["breakout", "momentum", "trend_continuation"],
            "size_multiplier": 0.5,
            "note": "Thin volume environment — stay mostly flat; fakeouts are likely on any move.",
        },
        "risk_on": {
            "max_positions": 6,
            "confidence_threshold": 0.55,
            "preferred_setups": ["breakout", "momentum", "sector_leader_long"],
            "avoid_setups": ["short_bias", "fade"],
            "size_multiplier": 1.1,
            "note": "Risk-on tape — favor tech/growth longs and sector leaders; avoid fighting the trend.",
        },
        "risk_off": {
            "max_positions": 3,
            "confidence_threshold": 0.65,
            "preferred_setups": ["defensive_rotation", "short_momentum"],
            "avoid_setups": ["growth_long", "breakout_long"],
            "size_multiplier": 0.6,
            "note": "Risk-off rotation — reduce exposure; defensive and short setups preferred.",
        },
        "sector_rotation": {
            "max_positions": 4,
            "confidence_threshold": 0.60,
            "preferred_setups": ["sector_leader_long", "relative_strength_breakout"],
            "avoid_setups": ["lagging_sector_long", "mean_reversion"],
            "size_multiplier": 1.0,
            "note": "Single sector leading — concentrate in the hot sector and ignore laggards.",
        },
        "unknown": {
            "max_positions": 1,
            "confidence_threshold": 0.85,
            "preferred_setups": [],
            "avoid_setups": ["breakout", "momentum", "mean_reversion", "fade"],
            "size_multiplier": 0.25,
            "note": "Regime unknown — stand aside; only the highest-conviction setups with minimal size.",
        },
    }

    return rules.get(
        regime,
        rules["unknown"],
    )
