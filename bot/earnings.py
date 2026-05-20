"""
Ascend Trader Elite — Earnings Calendar Engine

Detects upcoming earnings events for watchlist symbols and generates
specialised pre-earnings trade analysis prompts.  Primary data source is the
Alpaca corporate-actions endpoint; heuristic fallback is used when the API is
unavailable or returns no data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Earnings heuristic — approximate quarterly windows by symbol
# ---------------------------------------------------------------------------
# Each entry maps a symbol to a dict of quarters → (month, day) tuples that
# represent the *typical* earnings announcement date.  Dates are approximate
# (±1–2 weeks); they exist only to flag "earnings nearby" without any API.
#
# Coverage: all 23 watchlist symbols.
# Q1 = fiscal Q1 results (reported Jan–Feb)
# Q2 = fiscal Q2 results (reported Apr–May)
# Q3 = fiscal Q3 results (reported Jul–Aug)
# Q4 = fiscal Q4 / full-year results (reported Oct–Nov)
# ---------------------------------------------------------------------------

EARNINGS_HEURISTIC: dict[str, dict[str, tuple[int, int]]] = {
    # --- Mega cap ---
    "AAPL":  {"Q1": (2, 1),  "Q2": (5, 1),  "Q3": (8, 1),  "Q4": (11, 1)},
    "MSFT":  {"Q1": (1, 29), "Q2": (4, 29), "Q3": (7, 29), "Q4": (10, 29)},
    "AMZN":  {"Q1": (2, 3),  "Q2": (5, 3),  "Q3": (8, 3),  "Q4": (11, 3)},
    "GOOGL": {"Q1": (1, 31), "Q2": (4, 30), "Q3": (7, 30), "Q4": (10, 29)},
    "META":  {"Q1": (2, 2),  "Q2": (5, 2),  "Q3": (7, 31), "Q4": (10, 31)},
    # --- Semis ---
    "NVDA":  {"Q1": (2, 26), "Q2": (5, 28), "Q3": (8, 28), "Q4": (11, 20)},
    "AMD":   {"Q1": (1, 30), "Q2": (4, 30), "Q3": (7, 30), "Q4": (10, 29)},
    "SMCI":  {"Q1": (2, 6),  "Q2": (5, 7),  "Q3": (8, 6),  "Q4": (11, 5)},
    "ARM":   {"Q1": (2, 7),  "Q2": (5, 8),  "Q3": (8, 7),  "Q4": (11, 6)},
    # --- EV / auto ---
    "TSLA":  {"Q1": (1, 25), "Q2": (4, 23), "Q3": (7, 23), "Q4": (10, 23)},
    # --- Cybersecurity ---
    "CRWD":  {"Q1": (3, 5),  "Q2": (6, 4),  "Q3": (9, 4),  "Q4": (12, 3)},
    "PANW":  {"Q1": (2, 20), "Q2": (5, 20), "Q3": (8, 20), "Q4": (11, 19)},
    # --- Crypto-adjacent / fintech ---
    "COIN":  {"Q1": (2, 14), "Q2": (5, 7),  "Q3": (8, 6),  "Q4": (11, 6)},
    "HOOD":  {"Q1": (2, 13), "Q2": (5, 8),  "Q3": (8, 7),  "Q4": (11, 7)},
    "MSTR":  {"Q1": (2, 5),  "Q2": (5, 1),  "Q3": (8, 1),  "Q4": (10, 30)},
    "MARA":  {"Q1": (2, 27), "Q2": (5, 10), "Q3": (8, 9),  "Q4": (11, 8)},
    "RIOT":  {"Q1": (3, 12), "Q2": (5, 15), "Q3": (8, 14), "Q4": (11, 13)},
    "SOFI":  {"Q1": (1, 29), "Q2": (4, 29), "Q3": (7, 29), "Q4": (10, 28)},
    # --- Enterprise software ---
    "PLTR":  {"Q1": (2, 5),  "Q2": (5, 5),  "Q3": (8, 5),  "Q4": (11, 4)},
    # --- Volatile growth ---
    "IONQ":  {"Q1": (2, 28), "Q2": (5, 8),  "Q3": (8, 7),  "Q4": (11, 6)},
    "RKLB":  {"Q1": (2, 26), "Q2": (5, 13), "Q3": (8, 12), "Q4": (11, 11)},
    "RXRX":  {"Q1": (3, 4),  "Q2": (5, 8),  "Q3": (8, 7),  "Q4": (11, 6)},
    "ACHR":  {"Q1": (2, 20), "Q2": (5, 15), "Q3": (8, 14), "Q4": (11, 12)},
}

# ---------------------------------------------------------------------------
# Alpaca corporate-actions endpoint
# ---------------------------------------------------------------------------

ALPACA_CORP_ACTIONS_URL = (
    "https://data.alpaca.markets/v1beta1/corporate-actions"
)


async def fetch_earnings_calendar(
    symbols: list[str],
    alpaca_headers: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """
    Attempt to fetch upcoming earnings dates from the Alpaca corporate-actions
    API for each symbol in *symbols*.

    Returns a dict keyed by symbol; each value is a sub-dict:
      {
        "symbol":        str,
        "source":        "alpaca" | "heuristic" | "unknown",
        "earnings_date": date | None,
        "days_until":    int | None,
      }

    If the Alpaca call fails (network error, non-200, empty data), the
    heuristic fallback is applied automatically.
    """
    result: dict[str, dict[str, Any]] = {}
    today = date.today()

    # --- Try Alpaca first ---
    alpaca_data: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            start = today.isoformat()
            end   = (today + timedelta(days=30)).isoformat()
            resp  = await client.get(
                ALPACA_CORP_ACTIONS_URL,
                headers=alpaca_headers,
                params={
                    "symbols": ",".join(s.upper() for s in symbols),
                    "types":   "earnings_date",
                    "start":   start,
                    "end":     end,
                },
            )
            if resp.status_code == 200:
                payload = resp.json()
                # Alpaca returns {"earnings_dates": [{symbol, date, ...}, ...]}
                for item in payload.get("earnings_dates", []):
                    sym = item.get("symbol", "").upper()
                    raw_date = item.get("date") or item.get("earnings_date")
                    if sym and raw_date:
                        try:
                            alpaca_data[sym] = date.fromisoformat(
                                str(raw_date)[:10]
                            )
                        except ValueError:
                            pass
            else:
                logger.warning(
                    "Alpaca corporate-actions returned %d — falling back to heuristic.",
                    resp.status_code,
                )
    except httpx.HTTPError as exc:
        logger.warning(
            "Alpaca corporate-actions request failed (%s) — using heuristic.", exc
        )

    # --- Build result, filling gaps with heuristic ---
    for sym in symbols:
        sym = sym.upper()
        if sym in alpaca_data:
            earnings_date = alpaca_data[sym]
            source = "alpaca"
            delta  = (earnings_date - today).days
        else:
            earnings_date = _heuristic_next_earnings(sym, today)
            source = "heuristic" if earnings_date is not None else "unknown"
            delta  = (earnings_date - today).days if earnings_date else None

        result[sym] = {
            "symbol":        sym,
            "source":        source,
            "earnings_date": earnings_date,
            "days_until":    delta,
        }

    return result


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------


def _heuristic_next_earnings(
    symbol: str, today: date
) -> Optional[date]:
    """
    Return the next approximate earnings date for *symbol* using
    EARNINGS_HEURISTIC, or None if the symbol has no entry.
    """
    quarters = EARNINGS_HEURISTIC.get(symbol.upper())
    if not quarters:
        return None

    candidates: list[date] = []
    for year_offset in (0, 1):
        year = today.year + year_offset
        for _quarter, (month, day) in quarters.items():
            try:
                candidate = date(year, month, day)
                if candidate >= today:
                    candidates.append(candidate)
            except ValueError:
                # Feb 29 on non-leap year, etc.
                pass

    return min(candidates) if candidates else None


def days_until_earnings(symbol: str) -> Optional[int]:
    """
    Return approximate number of days until the next earnings event for
    *symbol* based on the heuristic calendar, or None if unknown.

    Returns negative values if the estimated date is in the past (stale data).
    """
    today = date.today()
    earnings_date = _heuristic_next_earnings(symbol.upper(), today)
    if earnings_date is None:
        return None
    return (earnings_date - today).days


def is_earnings_week(symbol: str, window_days: int = 5) -> bool:
    """
    Return True if *symbol* has an expected earnings event within the next
    *window_days* calendar days (default 5).
    """
    delta = days_until_earnings(symbol)
    if delta is None:
        return False
    return 0 <= delta <= window_days


# ---------------------------------------------------------------------------
# Pre-earnings prompt builder
# ---------------------------------------------------------------------------


def build_earnings_analysis_prompt(
    symbol: str,
    days_until: int,
    ind_1h: dict[str, Any],
    ind_1d: dict[str, Any],
    news: list[dict[str, Any]],
) -> str:
    """
    Build a specialised Claude prompt for evaluating a pre-earnings trade
    opportunity.

    Key differences from a standard trade prompt:
    - Position is taken BEFORE the announcement (not a play-through)
    - Tighter stops: 1.0× ATR (vs 1.5× standard)
    - Larger targets: 3.0× ATR
    - Beat/miss probability assessment from news + price action is required
    - Explicit earnings risk warning is embedded

    Parameters
    ----------
    symbol     : ticker, e.g. "NVDA"
    days_until : calendar days until expected earnings announcement
    ind_1h     : 1-hour indicator dict (same schema as risk_engine)
    ind_1d     : 1-day indicator dict
    news       : list of recent news items — each should have at least
                 {"headline": str, "summary": str, "published_at": str}
    """
    news_block = "\n".join(
        f"  [{i + 1}] ({item.get('published_at', 'n/a')}) "
        f"{item.get('headline', '')} — {item.get('summary', '')[:200]}"
        for i, item in enumerate(news[:8])
    ) or "  (no recent news available)"

    prompt = f"""You are an expert quantitative trader specialising in pre-earnings momentum plays.

=== PRE-EARNINGS TRADE ANALYSIS — {symbol.upper()} ===
Expected earnings in: {days_until} calendar day(s)

--- 1-Hour Indicators ---
RSI           : {ind_1h.get('rsi', 'N/A')}
MACD Hist     : {ind_1h.get('macd_hist', 'N/A')}
Above VWAP    : {ind_1h.get('above_vwap', 'N/A')}
Above EMA20   : {ind_1h.get('above_ema20', 'N/A')}
Volume Ratio  : {ind_1h.get('volume_ratio', 'N/A')}x avg
ATR (1h)      : {ind_1h.get('atr', 'N/A')}

--- Daily Indicators ---
RSI           : {ind_1d.get('rsi', 'N/A')}
MACD Hist     : {ind_1d.get('macd_hist', 'N/A')}
Above EMA50   : {ind_1d.get('above_ema50', 'N/A')}
Volume Ratio  : {ind_1d.get('volume_ratio', 'N/A')}x avg
ATR (1d)      : {ind_1d.get('atr', 'N/A')}

--- Recent News (last 48 h) ---
{news_block}

=== YOUR TASK ===
Analyse this pre-earnings opportunity and respond with a valid JSON object only.
No markdown, no prose outside the JSON.

Rules specific to PRE-EARNINGS plays:
1. Position must be entered BEFORE the announcement; close before or at open
   on earnings day to avoid binary event risk.
2. Stop-loss: 1.0× ATR (tighter than standard 1.5×) — earnings vol compression
   can reverse fast.
3. Take-profit: 3.0× ATR (wider than standard) — pre-earnings drift can be
   sharp and fast.
4. Assess BEAT probability and MISS probability from news sentiment, analyst
   revisions, price action, and options implied move if discernible.
5. Confidence must reflect BOTH the technical setup AND the earnings risk —
   penalise setups where news is mixed or price action contradicts consensus.

Required JSON schema:
{{
  "symbol":            "{symbol.upper()}",
  "action":            "buy" | "sell_short" | "no_trade",
  "confidence":        0.0–1.0,
  "beat_probability":  0.0–1.0,
  "miss_probability":  0.0–1.0,
  "entry_notes":       "brief entry trigger description",
  "stop_notes":        "stop placement rationale (1.0× ATR)",
  "target_notes":      "target rationale (3.0× ATR)",
  "key_risks":         ["risk1", "risk2", ...],
  "news_sentiment":    "bullish" | "bearish" | "neutral" | "mixed",
  "reasoning":         "≤3 sentence summary of the trade thesis"
}}
"""
    return prompt


# ---------------------------------------------------------------------------
# Opportunity scanner
# ---------------------------------------------------------------------------


async def get_pre_earnings_opportunities(
    symbols: list[str],
    alpaca_headers: dict[str, str],
    min_days: int = 1,
    max_days: int = 5,
) -> list[dict[str, Any]]:
    """
    Return a list of opportunity dicts for symbols with earnings in
    [*min_days*, *max_days*] calendar days.

    Each dict has the shape:
    {
        "symbol":        str,
        "days_until":    int,
        "earnings_date": date | None,
        "source":        "alpaca" | "heuristic" | "unknown",
    }

    Results are sorted by days_until ascending (most urgent first).
    """
    calendar = await fetch_earnings_calendar(symbols, alpaca_headers)

    opportunities: list[dict[str, Any]] = []
    for sym, info in calendar.items():
        delta = info.get("days_until")
        if delta is not None and min_days <= delta <= max_days:
            opportunities.append(
                {
                    "symbol":        sym,
                    "days_until":    delta,
                    "earnings_date": info.get("earnings_date"),
                    "source":        info.get("source", "unknown"),
                }
            )

    opportunities.sort(key=lambda x: x["days_until"])
    return opportunities
