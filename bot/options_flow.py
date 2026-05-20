"""
options_flow.py — Unusual Options Activity Detector for Ascend Trader Elite

Institutions telegraph their conviction via options before price moves. A
coordinated call sweep on NVDA with 10x normal volume and a short expiry is
not retail — it is someone betting large with information.

What we look for:
  1. Volume/OI ratio > 2x  — unusual participation vs open interest
  2. Large sweeps           — single orders filling across multiple exchanges
  3. Call/Put skew          — directional bias in the flow
  4. Expiry urgency         — near-term expirations = high conviction directional bet
  5. Premium size           — bigger premium = more institutional, less retail noise
  6. IV relative to norm    — IV crush after earnings vs IV expansion before move

Data source: Alpaca options chain API (/v2/options/contracts, /v2/options/snapshots)
Cache TTL: 15 minutes (options flow changes intraday)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_cache: dict[str, "OptionsFlowIntel"] = {}
_cache_built_at: dict[str, datetime] = {}
_CACHE_TTL_MINUTES = 15


def _cache_fresh(symbol: str) -> bool:
    built = _cache_built_at.get(symbol)
    if not built:
        return False
    return (datetime.now(timezone.utc) - built) < timedelta(minutes=_CACHE_TTL_MINUTES)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OptionsFlowSignal:
    option_type:   str    # "call" | "put"
    expiry:        str    # YYYY-MM-DD
    strike:        float
    volume:        int
    open_interest: int
    vol_oi_ratio:  float  # volume / open_interest — > 2 = unusual
    premium_usd:   float  # volume * mid_price * 100 (notional dollars)
    iv:            float  # implied volatility
    delta:         float  # option delta
    days_to_expiry: int
    is_sweep:      bool   # true if likely a sweep (large size, urgent)


@dataclass
class OptionsFlowIntel:
    symbol:           str
    flow_signal:      str    # "bullish" | "bearish" | "neutral" | "mixed"
    conviction_score: float  # 0-1 composite
    call_put_ratio:   float  # > 1 = call-heavy (bullish), < 1 = put-heavy (bearish)
    unusual_calls:    list[OptionsFlowSignal] = field(default_factory=list)
    unusual_puts:     list[OptionsFlowSignal] = field(default_factory=list)
    total_call_premium: float = 0.0   # $ notional in unusual calls
    total_put_premium:  float = 0.0   # $ notional in unusual puts
    dominant_expiry:  str = ""        # most active expiry date
    summary:          str = ""

    def as_dict(self) -> dict:
        return {
            "symbol":              self.symbol,
            "flow_signal":         self.flow_signal,
            "conviction_score":    self.conviction_score,
            "call_put_ratio":      self.call_put_ratio,
            "unusual_call_count":  len(self.unusual_calls),
            "unusual_put_count":   len(self.unusual_puts),
            "total_call_premium":  self.total_call_premium,
            "total_put_premium":   self.total_put_premium,
            "dominant_expiry":     self.dominant_expiry,
            "summary":             self.summary,
        }


# ---------------------------------------------------------------------------
# Alpaca options snapshot fetcher
# ---------------------------------------------------------------------------

async def _fetch_options_snapshot(
    symbol: str,
    alpaca_headers: dict,
    alpaca_data_url: str,
    client: httpx.AsyncClient,
) -> list[dict]:
    """
    Fetch options snapshot for a symbol from Alpaca.
    Returns list of contract snapshots with greeks and volume data.
    """
    # Get contracts expiring within 60 days
    today = date.today()
    max_expiry = today + timedelta(days=60)

    url = f"{alpaca_data_url}/v1beta1/options/snapshots/{symbol}"
    params = {
        "expiration_date_gte": today.isoformat(),
        "expiration_date_lte": max_expiry.isoformat(),
        "limit": 200,
        "feed": "indicative",
    }

    try:
        resp = await client.get(url, headers=alpaca_headers, params=params, timeout=15.0)
        if resp.status_code == 404:
            return []  # Options not available for this symbol
        resp.raise_for_status()
        data = resp.json()
        snapshots = data.get("snapshots", {})
        # snapshots is a dict keyed by OCC symbol
        return list(snapshots.values())
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 422):
            return []  # Options not available on paper/free tier
        logger.warning("Options snapshot failed for %s: %s", symbol, e)
        return []
    except Exception as exc:
        logger.warning("Options snapshot error for %s: %s", symbol, exc)
        return []


# ---------------------------------------------------------------------------
# Flow analysis
# ---------------------------------------------------------------------------

def _parse_snapshot(snap: dict) -> OptionsFlowSignal | None:
    """Parse a single contract snapshot into an OptionsFlowSignal."""
    try:
        details   = snap.get("details", {})
        greeks    = snap.get("greeks", {})
        day       = snap.get("day", {})
        latest_q  = snap.get("latestQuote", {})

        option_type = details.get("type", "").lower()  # "call" or "put"
        expiry_str  = details.get("expiration_date", "")
        strike      = float(details.get("strike_price", 0))

        volume = int(day.get("volume", 0) or 0)
        oi     = int(snap.get("openInterest", 1) or 1)

        bid = float(latest_q.get("bp", 0) or 0)
        ask = float(latest_q.get("ap", 0) or 0)
        mid = (bid + ask) / 2 if (bid + ask) > 0 else 0

        iv    = float(greeks.get("iv", 0) or 0)
        delta = float(greeks.get("delta", 0) or 0)

        if volume == 0 or strike == 0:
            return None

        vol_oi_ratio  = volume / max(oi, 1)
        premium_usd   = volume * mid * 100

        # Expiry urgency
        try:
            expiry_date   = date.fromisoformat(expiry_str)
            days_to_exp   = (expiry_date - date.today()).days
        except (ValueError, TypeError):
            days_to_exp = 30

        # Sweep heuristic: large volume, near-money, short-dated
        is_sweep = (
            vol_oi_ratio > 3.0
            and volume > 500
            and days_to_exp <= 21
            and 0.20 <= abs(delta) <= 0.80
        )

        return OptionsFlowSignal(
            option_type    = option_type,
            expiry         = expiry_str,
            strike         = strike,
            volume         = volume,
            open_interest  = oi,
            vol_oi_ratio   = round(vol_oi_ratio, 2),
            premium_usd    = round(premium_usd, 0),
            iv             = round(iv, 4),
            delta          = round(delta, 4),
            days_to_expiry = days_to_exp,
            is_sweep       = is_sweep,
        )
    except Exception:
        return None


def _analyze_flow(snapshots: list[dict], symbol: str) -> OptionsFlowIntel:
    """
    Process all contract snapshots and derive a flow signal.
    """
    all_signals = [_parse_snapshot(s) for s in snapshots]
    all_signals = [s for s in all_signals if s is not None]

    if not all_signals:
        return OptionsFlowIntel(
            symbol=symbol, flow_signal="neutral", conviction_score=0.0,
            call_put_ratio=1.0, summary=f"No options data for {symbol}.",
        )

    # Split calls and puts
    calls = [s for s in all_signals if s.option_type == "call"]
    puts  = [s for s in all_signals if s.option_type == "put"]

    # Total volume by type
    call_vol = sum(s.volume for s in calls)
    put_vol  = sum(s.volume for s in puts)
    total_vol = call_vol + put_vol

    call_put_ratio = call_vol / max(put_vol, 1)

    # Unusual activity: vol/OI > 2 threshold
    UNUSUAL_THRESHOLD = 2.0
    unusual_calls = sorted(
        [s for s in calls if s.vol_oi_ratio >= UNUSUAL_THRESHOLD],
        key=lambda x: x.premium_usd, reverse=True,
    )[:10]
    unusual_puts = sorted(
        [s for s in puts if s.vol_oi_ratio >= UNUSUAL_THRESHOLD],
        key=lambda x: x.premium_usd, reverse=True,
    )[:10]

    call_premium = sum(s.premium_usd for s in unusual_calls)
    put_premium  = sum(s.premium_usd for s in unusual_puts)

    # Dominant expiry (most active)
    expiry_vol: dict[str, int] = {}
    for s in all_signals:
        expiry_vol[s.expiry] = expiry_vol.get(s.expiry, 0) + s.volume
    dominant_expiry = max(expiry_vol, key=expiry_vol.get) if expiry_vol else ""

    # Sweep count
    sweep_calls = [s for s in unusual_calls if s.is_sweep]
    sweep_puts  = [s for s in unusual_puts  if s.is_sweep]

    # ---- Conviction score (0-1) ----
    # Component 1: call/put volume skew
    if call_put_ratio > 2.0:
        direction_score = 1.0
        direction = "bullish"
    elif call_put_ratio > 1.3:
        direction_score = 0.6
        direction = "bullish"
    elif call_put_ratio < 0.5:
        direction_score = 1.0
        direction = "bearish"
    elif call_put_ratio < 0.75:
        direction_score = 0.6
        direction = "bearish"
    else:
        direction_score = 0.0
        direction = "neutral"

    # Component 2: premium size (larger = more institutional)
    dominant_premium = max(call_premium, put_premium)
    if dominant_premium >= 5_000_000:
        premium_score = 1.0
    elif dominant_premium >= 1_000_000:
        premium_score = 0.7
    elif dominant_premium >= 250_000:
        premium_score = 0.4
    else:
        premium_score = 0.1

    # Component 3: sweeps present
    sweep_count = len(sweep_calls) if direction == "bullish" else len(sweep_puts)
    sweep_score = min(sweep_count / 3.0, 1.0)

    # Component 4: unusual contract count
    unusual_count = len(unusual_calls) if direction == "bullish" else len(unusual_puts)
    unusual_score = min(unusual_count / 5.0, 1.0)

    conviction_score = round(
        direction_score * 0.30
        + premium_score * 0.35
        + sweep_score   * 0.20
        + unusual_score * 0.15,
        4,
    )

    # Mixed signal if both calls and puts are unusual
    if len(unusual_calls) >= 2 and len(unusual_puts) >= 2 and call_put_ratio < 1.5:
        flow_signal = "mixed"
    else:
        flow_signal = direction if conviction_score >= 0.25 else "neutral"

    # Human summary
    top_call = unusual_calls[0] if unusual_calls else None
    top_put  = unusual_puts[0]  if unusual_puts  else None

    lines = []
    if flow_signal == "bullish":
        lines.append(
            f"BULLISH options flow: {len(unusual_calls)} unusual call contracts, "
            f"${call_premium:,.0f} total premium. Call/Put ratio: {call_put_ratio:.1f}x."
        )
        if sweep_calls:
            lines.append(f"{len(sweep_calls)} call sweep(s) detected — likely institutional.")
        if top_call:
            lines.append(
                f"Largest: {top_call.strike:.0f}C exp {top_call.expiry} | "
                f"vol={top_call.volume:,} vs OI={top_call.open_interest:,} "
                f"({top_call.vol_oi_ratio:.1f}x) | ${top_call.premium_usd:,.0f} premium | "
                f"delta={top_call.delta:.2f}"
            )
    elif flow_signal == "bearish":
        lines.append(
            f"BEARISH options flow: {len(unusual_puts)} unusual put contracts, "
            f"${put_premium:,.0f} total premium. Call/Put ratio: {call_put_ratio:.1f}x."
        )
        if sweep_puts:
            lines.append(f"{len(sweep_puts)} put sweep(s) detected — likely institutional hedging/shorting.")
        if top_put:
            lines.append(
                f"Largest: {top_put.strike:.0f}P exp {top_put.expiry} | "
                f"vol={top_put.volume:,} vs OI={top_put.open_interest:,} "
                f"({top_put.vol_oi_ratio:.1f}x) | ${top_put.premium_usd:,.0f} premium | "
                f"delta={top_put.delta:.2f}"
            )
    elif flow_signal == "mixed":
        lines.append(
            f"MIXED options flow: {len(unusual_calls)} unusual calls (${call_premium:,.0f}) "
            f"vs {len(unusual_puts)} unusual puts (${put_premium:,.0f}). "
            f"Market is uncertain — conflicting institutional positioning."
        )
    else:
        lines.append(f"No unusual options activity detected for {symbol}.")

    return OptionsFlowIntel(
        symbol              = symbol,
        flow_signal         = flow_signal,
        conviction_score    = conviction_score,
        call_put_ratio      = round(call_put_ratio, 3),
        unusual_calls       = unusual_calls,
        unusual_puts        = unusual_puts,
        total_call_premium  = call_premium,
        total_put_premium   = put_premium,
        dominant_expiry     = dominant_expiry,
        summary             = " ".join(lines),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_options_flow_intel(
    watchlist: list[str],
    alpaca_headers: dict,
    alpaca_data_url: str,
) -> dict[str, OptionsFlowIntel]:
    """
    Fetch and analyze options flow for all watchlist symbols.
    Returns dict of symbol → OptionsFlowIntel. Cached 15 minutes.
    """
    missing = [s for s in watchlist if not _cache_fresh(s)]
    if not missing:
        return {s: _cache[s] for s in watchlist if s in _cache}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_fetch_and_analyze(sym, alpaca_headers, alpaca_data_url, client)
              for sym in missing],
            return_exceptions=True,
        )

    for sym, result in zip(missing, results):
        if isinstance(result, OptionsFlowIntel):
            _cache[sym] = result
            _cache_built_at[sym] = datetime.now(timezone.utc)
        else:
            neutral = OptionsFlowIntel(
                symbol=sym, flow_signal="neutral", conviction_score=0.0,
                call_put_ratio=1.0,
                summary=f"Options flow unavailable: {result}",
            )
            _cache[sym] = neutral
            _cache_built_at[sym] = datetime.now(timezone.utc)

    return {s: _cache[s] for s in watchlist if s in _cache}


async def _fetch_and_analyze(
    symbol: str,
    alpaca_headers: dict,
    alpaca_data_url: str,
    client: httpx.AsyncClient,
) -> OptionsFlowIntel:
    snapshots = await _fetch_options_snapshot(symbol, alpaca_headers, alpaca_data_url, client)
    return _analyze_flow(snapshots, symbol)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_options_flow_prompt_section(intel: dict[str, OptionsFlowIntel], symbol: str) -> str:
    """Format options flow intel for Claude prompt injection."""
    info = intel.get(symbol)
    if info is None or info.flow_signal == "neutral":
        return f"OPTIONS FLOW [{symbol}]: No unusual activity detected."

    signal_label = {
        "bullish": "UNUSUAL CALL BUYING",
        "bearish": "UNUSUAL PUT BUYING",
        "mixed":   "CONFLICTING FLOW",
    }.get(info.flow_signal, "NEUTRAL")

    return (
        f"OPTIONS FLOW [{symbol}]: {signal_label} | "
        f"Conviction: {info.conviction_score:.0%} | "
        f"C/P ratio: {info.call_put_ratio:.1f}x\n"
        f"{info.summary}"
    )


# ---------------------------------------------------------------------------
# Confidence boost
# ---------------------------------------------------------------------------

def options_flow_confidence_boost(
    intel: dict[str, OptionsFlowIntel],
    symbol: str,
    signal_side: str,
) -> float:
    """
    Confidence adjustment based on options flow alignment.

    Long + bullish flow:   +0.05 to +0.10 (scales with conviction)
    Short + bearish flow:  +0.05 to +0.10
    Long + bearish flow:   -0.08 (smart money betting against you)
    Short + bullish flow:  -0.08
    Mixed flow:            -0.03 (uncertainty penalty)
    Neutral:                0.00
    """
    info = intel.get(symbol)
    if info is None:
        return 0.0

    raw = signal_side.lower()
    if raw not in ("buy", "long", "sell", "short"):
        return 0.0
    is_long = raw in ("buy", "long")
    flow    = info.flow_signal
    conv    = info.conviction_score

    if flow == "bullish":
        return round(+conv * 0.10, 4) if is_long else -0.08
    if flow == "bearish":
        return round(+conv * 0.10, 4) if not is_long else -0.08
    if flow == "mixed":
        return -0.03
    return 0.0
