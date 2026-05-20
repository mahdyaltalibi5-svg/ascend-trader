"""
relative_strength.py — Relative Strength Engine for Ascend Trader Elite

The core question is not "is NVDA going up?" but "is NVDA outperforming
everything else?" Relative strength separates real leaders from rising tide
moves — and real laggards from falling knives worth shorting.

All functions are async-compatible with graceful fallback on any error.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector ETF mapping — each symbol maps to its benchmark ETF
# ---------------------------------------------------------------------------

SECTOR_ETF_MAP: dict[str, str] = {
    # Semis → SMH
    "NVDA": "SMH",
    "AMD":  "SMH",
    "SMCI": "SMH",
    "ARM":  "SMH",
    # Mega-cap tech → XLK
    "AAPL":  "XLK",
    "MSFT":  "XLK",
    "META":  "XLK",
    "GOOGL": "XLK",
    "AMZN":  "XLK",
    # Enterprise sw / cybersec / fintech → QQQ
    "PLTR": "QQQ",
    "CRWD": "QQQ",
    "PANW": "QQQ",
    "SOFI": "QQQ",
    # Crypto-adjacent → QQQ proxy
    "COIN": "QQQ",
    "HOOD": "QQQ",
    "MSTR": "QQQ",
    "MARA": "QQQ",
    "RIOT": "QQQ",
    # EV / auto → QQQ
    "TSLA": "QQQ",
    "RIVN": "QQQ",
    "ACHR": "QQQ",
    # Volatile growth → ARKK
    "IONQ": "ARKK",
    "RKLB": "ARKK",
    "RXRX": "ARKK",
}

# Universal benchmarks always fetched
UNIVERSAL_BENCHMARKS = ["SPY", "QQQ", "SMH", "XLK", "ARKK", "IWM"]


# ---------------------------------------------------------------------------
# Bar fetching
# ---------------------------------------------------------------------------

async def _fetch_bars_single(
    symbol: str,
    alpaca_headers: dict,
    alpaca_data_url: str,
    timeframe: str,
    limit: int,
    client: httpx.AsyncClient,
) -> tuple[str, pd.DataFrame]:
    """Fetch OHLCV bars for a single symbol. Returns (symbol, DataFrame)."""
    url = f"{alpaca_data_url}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "adjustment": "raw",
        "feed": "iex",
    }
    try:
        resp = await client.get(url, headers=alpaca_headers, params=params, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        bars = data.get("bars", [])
        if not bars:
            return symbol, pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = df.sort_values("t").reset_index(drop=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return symbol, df
    except Exception as exc:
        logger.warning("RS bar fetch failed for %s: %s", symbol, exc)
        return symbol, pd.DataFrame()


async def fetch_rs_bars(
    symbols: list[str],
    alpaca_headers: dict,
    alpaca_data_url: str,
    timeframe: str = "1Day",
    limit: int = 30,
) -> dict[str, pd.DataFrame]:
    """
    Fetch daily bars for all symbols + their sector ETFs concurrently.

    Returns a dict of { symbol_or_etf → DataFrame }.
    """
    # Collect all symbols needed: watchlist + their ETFs + universal benchmarks
    etfs_needed = set(UNIVERSAL_BENCHMARKS)
    for sym in symbols:
        etf = SECTOR_ETF_MAP.get(sym)
        if etf:
            etfs_needed.add(etf)

    all_symbols = list(set(symbols) | etfs_needed)

    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_bars_single(sym, alpaca_headers, alpaca_data_url, timeframe, limit, client)
            for sym in all_symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    bars: dict[str, pd.DataFrame] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("RS gather exception: %s", result)
            continue
        sym, df = result
        bars[sym] = df

    return bars


# ---------------------------------------------------------------------------
# Core RS math
# ---------------------------------------------------------------------------

def compute_return_pct(df: pd.DataFrame, periods: int) -> float:
    """
    N-bar percentage return using closing prices.

    Returns 0.0 if the DataFrame has fewer than (periods + 1) rows or is empty.
    """
    if df is None or df.empty or len(df) < periods + 1:
        return 0.0
    try:
        end_price   = float(df["close"].iloc[-1])
        start_price = float(df["close"].iloc[-(periods + 1)])
        if start_price == 0:
            return 0.0
        return (end_price - start_price) / start_price * 100.0
    except Exception:
        return 0.0


def compute_rs_score(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    periods: int = 5,
) -> float:
    """
    Symbol return minus benchmark return over N periods.

    Positive = outperforming. Negative = underperforming.
    Returns 0.0 on any error or insufficient data.
    """
    try:
        sym_ret   = compute_return_pct(symbol_df, periods)
        bench_ret = compute_return_pct(benchmark_df, periods)
        return sym_ret - bench_ret
    except Exception:
        return 0.0


def compute_rs_rank(
    symbol: str,
    all_bars: dict[str, pd.DataFrame],
    periods: int = 5,
) -> float:
    """
    Percentile rank (0-1) of this symbol's 5-day RS-vs-SPY among all watchlist symbols.

    1.0 = strongest RS in the watchlist. 0.0 = weakest.
    Returns 0.5 on error.
    """
    try:
        spy_df = all_bars.get("SPY", pd.DataFrame())
        if spy_df.empty:
            return 0.5

        # Compute RS for every watchlist symbol (skip ETFs / benchmarks)
        watchlist_syms = [s for s in all_bars if s not in set(UNIVERSAL_BENCHMARKS)]
        if not watchlist_syms:
            return 0.5

        scores: dict[str, float] = {}
        for sym in watchlist_syms:
            sym_df = all_bars.get(sym, pd.DataFrame())
            scores[sym] = compute_rs_score(sym_df, spy_df, periods)

        if symbol not in scores:
            return 0.5

        all_scores = list(scores.values())
        sym_score  = scores[symbol]
        rank       = sum(1 for s in all_scores if s <= sym_score) / len(all_scores)
        return round(rank, 4)
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# High-level RS intelligence
# ---------------------------------------------------------------------------

async def get_relative_strength_intel(
    watchlist: list[str],
    alpaca_headers: dict,
    alpaca_data_url: str,
) -> dict[str, dict]:
    """
    For each symbol in the watchlist, compute and return:

      rs_vs_spy_5d   — outperformance vs SPY over 5 days (pct pts)
      rs_vs_sector_5d — outperformance vs sector ETF over 5 days
      rs_vs_spy_20d  — outperformance vs SPY over 20 days
      rs_rank        — 0-1 percentile among watchlist symbols (1=best)
      rs_trend       — "improving" | "deteriorating" | "stable"
      rs_signal      — "leader" | "laggard" | "neutral"
      rs_note        — one-sentence summary
    """
    all_bars = await fetch_rs_bars(watchlist, alpaca_headers, alpaca_data_url, limit=30)
    spy_df   = all_bars.get("SPY", pd.DataFrame())

    intel: dict[str, dict] = {}

    for symbol in watchlist:
        try:
            sym_df     = all_bars.get(symbol, pd.DataFrame())
            sector_etf = SECTOR_ETF_MAP.get(symbol, "QQQ")
            sector_df  = all_bars.get(sector_etf, pd.DataFrame())

            rs_spy_5d    = compute_rs_score(sym_df, spy_df,    periods=5)
            rs_sector_5d = compute_rs_score(sym_df, sector_df, periods=5)
            rs_spy_20d   = compute_rs_score(sym_df, spy_df,    periods=20)
            rs_rank      = compute_rs_rank(symbol, all_bars,   periods=5)

            # Trend: compare recent 5-day RS to the 20-day RS baseline
            if rs_spy_5d > rs_spy_20d + 0.5:
                rs_trend = "improving"
            elif rs_spy_5d < rs_spy_20d - 0.5:
                rs_trend = "deteriorating"
            else:
                rs_trend = "stable"

            # Signal classification
            if rs_rank >= 0.70 and rs_spy_5d > 0:
                rs_signal = "leader"
            elif rs_rank <= 0.30 and rs_spy_5d < 0:
                rs_signal = "laggard"
            else:
                rs_signal = "neutral"

            # Human-readable note
            sign_spy    = "+" if rs_spy_5d >= 0 else ""
            sign_sector = "+" if rs_sector_5d >= 0 else ""
            rank_pct    = int(rs_rank * 100)
            trend_clause = {
                "improving":    "RS trend is improving — accelerating leadership.",
                "deteriorating":"RS trend is deteriorating — losing relative strength.",
                "stable":       "RS trend is stable — consistent relative performance.",
            }[rs_trend]

            if rs_signal == "leader":
                label = "MARKET LEADER"
            elif rs_signal == "laggard":
                label = "MARKET LAGGARD"
            else:
                label = "NEUTRAL"

            rs_note = (
                f"{symbol} is {'outperforming' if rs_spy_5d >= 0 else 'underperforming'} "
                f"SPY by {sign_spy}{rs_spy_5d:.1f}% over 5 days and "
                f"{'outperforming' if rs_sector_5d >= 0 else 'underperforming'} "
                f"{sector_etf} by {sign_sector}{rs_sector_5d:.1f}%; "
                f"RS rank {rank_pct}th percentile — {label}."
            )

            intel[symbol] = {
                "rs_vs_spy_5d":    round(rs_spy_5d,    3),
                "rs_vs_sector_5d": round(rs_sector_5d, 3),
                "rs_vs_spy_20d":   round(rs_spy_20d,   3),
                "rs_rank":         rs_rank,
                "rs_trend":        rs_trend,
                "rs_signal":       rs_signal,
                "rs_note":         rs_note,
                "sector_etf":      sector_etf,
            }

        except Exception as exc:
            logger.warning("RS intel failed for %s: %s", symbol, exc)
            intel[symbol] = _neutral_rs(symbol)

    return intel


def _neutral_rs(symbol: str) -> dict:
    """Fallback neutral RS intel for error cases."""
    return {
        "rs_vs_spy_5d":    0.0,
        "rs_vs_sector_5d": 0.0,
        "rs_vs_spy_20d":   0.0,
        "rs_rank":         0.5,
        "rs_trend":        "stable",
        "rs_signal":       "neutral",
        "rs_note":         f"{symbol}: RS data unavailable — treating as neutral.",
        "sector_etf":      SECTOR_ETF_MAP.get(symbol, "QQQ"),
    }


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def build_rs_prompt_section(rs_intel: dict, symbol: str) -> str:
    """
    Format RS intelligence for the Claude prompt.

    Example output:
      RELATIVE STRENGTH: NVDA is outperforming SPY by +4.2% over 5 days,
      outperforming SMH (semis) by +1.8%. RS rank: 92nd percentile among all
      watchlist symbols. RS trend is improving — accelerating leadership.
      This is a MARKET LEADER setup.
    """
    info = rs_intel.get(symbol, _neutral_rs(symbol))

    rs_spy    = info.get("rs_vs_spy_5d", 0.0)
    rs_sector = info.get("rs_vs_sector_5d", 0.0)
    rs_spy20  = info.get("rs_vs_spy_20d", 0.0)
    rs_rank   = info.get("rs_rank", 0.5)
    rs_trend  = info.get("rs_trend", "stable")
    rs_signal = info.get("rs_signal", "neutral")
    etf       = info.get("sector_etf", "QQQ")

    rank_pct = int(rs_rank * 100)

    spy_word    = "outperforming" if rs_spy >= 0    else "underperforming"
    sector_word = "outperforming" if rs_sector >= 0 else "underperforming"
    spy_sign    = "+" if rs_spy >= 0    else ""
    sector_sign = "+" if rs_sector >= 0 else ""
    spy20_sign  = "+" if rs_spy20 >= 0  else ""

    trend_desc = {
        "improving":    "RS trend is improving — accelerating leadership.",
        "deteriorating":"RS trend is deteriorating — momentum fading relative to market.",
        "stable":       "RS trend is stable — consistent with recent baseline.",
    }.get(rs_trend, "RS trend is stable.")

    signal_line = {
        "leader":  "This is a MARKET LEADER setup.",
        "laggard": "This is a MARKET LAGGARD — caution on longs, consider short.",
        "neutral": "RS is NEUTRAL — no strong leadership or laggard signal.",
    }.get(rs_signal, "RS is NEUTRAL.")

    return (
        f"RELATIVE STRENGTH: {symbol} is {spy_word} SPY by "
        f"{spy_sign}{rs_spy:.1f}% over 5 days, {sector_word} {etf} (sector) by "
        f"{sector_sign}{rs_sector:.1f}%. 20-day RS vs SPY: {spy20_sign}{rs_spy20:.1f}%. "
        f"RS rank: {rank_pct}th percentile among all watchlist symbols. "
        f"{trend_desc} {signal_line}"
    )


# ---------------------------------------------------------------------------
# Signal boost
# ---------------------------------------------------------------------------

def rs_score_boost(rs_intel: dict, symbol: str, signal_side: str) -> float:
    """
    Composite confidence boost or penalty based on relative strength alignment.

    signal_side: "buy"/"long" or "sell"/"short"

    Returns:
      +0.08  buying a market leader (long + leader)
      +0.08  shorting a laggard     (short + laggard)
      -0.10  buying a laggard       (long + laggard)
      -0.10  shorting a leader      (short + leader)
       0.00  neutral / unknown
    """
    info = rs_intel.get(symbol, {})
    rs_signal = info.get("rs_signal", "neutral")
    # Normalise: Alpaca signals are "buy"/"sell"; internal may be "long"/"short"
    raw = signal_side.lower()
    side = "long" if raw in ("buy", "long") else ("short" if raw in ("sell", "short") else raw)

    if rs_signal == "leader":
        return +0.08 if side == "long" else -0.10
    if rs_signal == "laggard":
        return +0.08 if side == "short" else -0.10
    return 0.0
