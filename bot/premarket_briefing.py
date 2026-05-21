"""
premarket_briefing.py — Daily pre-market briefing generator for Ascend Trader.

Runs at 8 AM ET before market open. Fetches pre-market bar data, computes
gap percentages, pulls relevant news, classifies the current regime, and
uses Claude to write a structured morning briefing saved to Supabase.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from regime_brain import get_full_regime

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------

async def _fetch_premarket_bars(
    symbol: str,
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
    session: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    """Fetch 5-minute pre-market bars from 4 AM ET to now for a symbol."""
    now_et = datetime.now(ET)
    today_et = now_et.date()
    start_et = datetime(today_et.year, today_et.month, today_et.day, 4, 0, 0, tzinfo=ET)
    start_iso = start_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{alpaca_data_url}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": "5Min",
        "start": start_iso,
        "limit": 50,
        "feed": "iex",
    }
    try:
        resp = await session.get(url, headers=alpaca_headers, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json().get("bars", [])
    except Exception as exc:
        logger.warning("premarket_briefing: bar fetch failed for %s — %s", symbol, exc)
        return []


async def _fetch_prev_close(
    symbol: str,
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
    session: httpx.AsyncClient,
) -> float | None:
    """Fetch the most recent prior-day close for a symbol."""
    url = f"{alpaca_data_url}/v2/stocks/{symbol}/bars"
    now_et = datetime.now(ET)
    # Use yesterday's date to get the prior session close
    yesterday = (now_et - timedelta(days=3)).date().isoformat()
    params = {
        "timeframe": "1Day",
        "start": yesterday,
        "limit": 5,
        "feed": "iex",
    }
    try:
        resp = await session.get(url, headers=alpaca_headers, params=params, timeout=10.0)
        resp.raise_for_status()
        bars = resp.json().get("bars", [])
        if bars:
            return float(bars[-1]["c"])
    except Exception as exc:
        logger.warning("premarket_briefing: prev close fetch failed for %s — %s", symbol, exc)
    return None


async def _fetch_news(
    symbol: str,
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
    session: httpx.AsyncClient,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch the latest news headlines for a symbol via Alpaca news endpoint."""
    # News endpoint base differs from data — strip /v2 path prefix if present
    base = alpaca_data_url.rstrip("/")
    url = f"{base}/v1beta1/news"
    params = {"symbols": symbol, "limit": limit}
    try:
        resp = await session.get(url, headers=alpaca_headers, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json().get("news", [])
    except Exception as exc:
        logger.warning("premarket_briefing: news fetch failed for %s — %s", symbol, exc)
        return []


# ---------------------------------------------------------------------------
# Gap computation
# ---------------------------------------------------------------------------

def _compute_gap(bars: list[dict[str, Any]], prev_close: float | None) -> dict[str, Any]:
    """Return gap_pct and pre-market volume given intraday bars and prior close."""
    if not bars:
        return {"gap_pct": 0.0, "premarket_vol": 0}

    latest_price = float(bars[-1].get("c") or bars[-1].get("o", 0))
    total_vol = sum(int(b.get("v", 0)) for b in bars)

    if prev_close and prev_close > 0:
        gap_pct = ((latest_price - prev_close) / prev_close) * 100
    else:
        gap_pct = 0.0

    return {
        "gap_pct": round(gap_pct, 2),
        "premarket_price": round(latest_price, 4),
        "premarket_vol": total_vol,
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_briefing_prompt(
    regime: dict[str, Any],
    top_movers: list[dict[str, Any]],
    news_by_symbol: dict[str, list[str]],
) -> str:
    mover_lines = "\n".join(
        f"  {m['symbol']}: gap={m['gap_pct']:+.2f}%, pre-mkt vol={m['premarket_vol']:,}"
        for m in top_movers
    )

    news_lines_parts: list[str] = []
    for sym, headlines in news_by_symbol.items():
        if headlines:
            news_lines_parts.append(f"  {sym}:")
            for h in headlines:
                news_lines_parts.append(f"    - {h}")
    news_lines = "\n".join(news_lines_parts) if news_lines_parts else "  (no news available)"

    return f"""You are a pre-market analyst for an algorithmic equity trading bot.

MARKET REGIME:
{regime}

PRE-MARKET TOP MOVERS (by absolute gap %):
{mover_lines}

RECENT NEWS FOR TOP MOVERS:
{news_lines}

Write a structured morning briefing in exactly 3 paragraphs:
1. Overall market read for today — what the regime signals and what kind of trading day to expect.
2. Top 3 specific trade setups to watch, each with a symbol, direction, and key price levels.
3. Main risks to avoid today — macro, technical, or regime-specific hazards.

Be concise, data-driven, and actionable. No filler. Total response should be 200–350 words."""


# ---------------------------------------------------------------------------
# Supabase persistence
# ---------------------------------------------------------------------------

async def _save_briefing(
    supabase: Any,
    briefing_text: str,
    top_movers: list[dict[str, Any]],
    regime_snapshot: dict[str, Any],
    generated_at: str,
) -> None:
    try:
        await asyncio.to_thread(
            lambda: supabase.table("morning_briefings").insert({
                "briefing_text": briefing_text,
                "top_movers": top_movers,
                "regime_snapshot": regime_snapshot,
                "generated_at": generated_at,
            }).execute()
        )
        logger.info("premarket_briefing: saved to morning_briefings.")
    except Exception as exc:
        logger.error("premarket_briefing: Supabase insert failed — %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_morning_briefing(
    watchlist: list[str],
    alpaca_headers: dict[str, str],
    alpaca_data_url: str,
    claude_client: Any,
    supabase: Any,
) -> dict[str, Any]:
    """
    Generate the daily pre-market briefing.

    Fetches pre-market bars and news, classifies the regime, calls Claude
    for a written briefing, and persists the result to Supabase.

    Returns a dict with keys:
      briefing_text, top_movers, regime_snapshot, generated_at
    """
    generated_at = datetime.now(timezone.utc).isoformat()

    # Fetch regime
    try:
        regime = await get_full_regime(alpaca_headers, alpaca_data_url)
    except Exception as exc:
        logger.error("premarket_briefing: regime fetch failed — %s", exc)
        regime = {"regime": "unknown"}

    # Fetch pre-market data concurrently
    async with httpx.AsyncClient() as session:
        bar_tasks = {
            sym: asyncio.create_task(
                _fetch_premarket_bars(sym, alpaca_headers, alpaca_data_url, session)
            )
            for sym in watchlist
        }
        close_tasks = {
            sym: asyncio.create_task(
                _fetch_prev_close(sym, alpaca_headers, alpaca_data_url, session)
            )
            for sym in watchlist
        }

        bar_results: dict[str, list[dict[str, Any]]] = {}
        for sym, task in bar_tasks.items():
            bar_results[sym] = await task

        close_results: dict[str, float | None] = {}
        for sym, task in close_tasks.items():
            close_results[sym] = await task

        # Compute gap info for each symbol
        mover_data: list[dict[str, Any]] = []
        for sym in watchlist:
            gap_info = _compute_gap(bar_results.get(sym, []), close_results.get(sym))
            mover_data.append({"symbol": sym, **gap_info})

        # Sort by absolute gap, take top 5
        mover_data.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
        top_movers = mover_data[:5]

        # Fetch news for top 5 movers
        news_tasks = {
            m["symbol"]: asyncio.create_task(
                _fetch_news(m["symbol"], alpaca_headers, alpaca_data_url, session)
            )
            for m in top_movers
        }
        news_by_symbol: dict[str, list[str]] = {}
        for sym, task in news_tasks.items():
            articles = await task
            news_by_symbol[sym] = [a.get("headline", "") for a in articles if a.get("headline")]

    # Claude briefing
    briefing_text = ""
    if claude_client is None:
        logger.warning("premarket_briefing: claude_client is None, skipping Claude call.")
        briefing_text = "Pre-market briefing unavailable — Claude client not configured."
    else:
        prompt = _build_briefing_prompt(regime, top_movers, news_by_symbol)
        try:
            response = await asyncio.to_thread(
                claude_client.messages.create,
                model="claude-sonnet-4-6",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            briefing_text = response.content[0].text.strip()
        except Exception as exc:
            logger.error("premarket_briefing: Claude call failed — %s", exc)
            briefing_text = "Pre-market briefing generation failed. Check logs."

    result: dict[str, Any] = {
        "briefing_text": briefing_text,
        "top_movers": top_movers,
        "regime_snapshot": regime,
        "generated_at": generated_at,
    }

    if supabase is not None:
        await _save_briefing(
            supabase,
            briefing_text=briefing_text,
            top_movers=top_movers,
            regime_snapshot=regime,
            generated_at=generated_at,
        )

    return result


async def get_latest_briefing(supabase: Any) -> dict[str, Any] | None:
    """
    Read the most recent morning_briefings row from Supabase.

    Returns None if the table is empty, Supabase is unavailable, or any
    error occurs.
    """
    if supabase is None:
        logger.warning("premarket_briefing: supabase is None, cannot fetch latest briefing.")
        return None
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("morning_briefings")
            .select("*")
            .order("generated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data
        if rows:
            return rows[0]
    except Exception as exc:
        logger.error("premarket_briefing: get_latest_briefing failed — %s", exc)
    return None
