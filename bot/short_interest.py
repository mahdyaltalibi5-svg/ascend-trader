"""
short_interest.py — Short Interest & Squeeze Potential Tracker

High short interest + a catalyst = explosive squeeze potential. This is one
of the most reliable setups in momentum trading — when shorts are forced to
cover into a rising stock, they become your buyers.

Data source: Finviz screener (free, no API key required).
  URL: https://finviz.com/quote.ashx?t={SYMBOL}

Key metrics extracted:
  - Short Float %   : percentage of float sold short
  - Short Ratio     : days to cover (short interest / avg daily volume)
  - Float shares    : actual tradeable float size
  - Institutional % : institutional ownership

Squeeze scoring:
  - Short float > 20% = high squeeze potential
  - Days to cover > 5 = forced covering takes multiple days = sustained move
  - Plus a catalyst (news, earnings, breakout) = ideal setup

Cache TTL: 4 hours (Finviz data updates twice daily)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_cache: dict[str, "ShortIntel"] = {}
_cache_built_at: dict[str, datetime] = {}
_CACHE_TTL_HOURS = 4

_FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _cache_fresh(symbol: str) -> bool:
    built = _cache_built_at.get(symbol)
    if not built:
        return False
    return (datetime.now(timezone.utc) - built) < timedelta(hours=_CACHE_TTL_HOURS)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ShortIntel:
    symbol:           str
    short_float_pct:  float | None   # % of float sold short
    short_ratio:      float | None   # days to cover
    float_shares_m:   float | None   # float in millions
    inst_own_pct:     float | None   # institutional ownership %
    squeeze_score:    float          # 0-1 composite squeeze potential
    squeeze_signal:   str            # "extreme" | "high" | "moderate" | "low"
    summary:          str

    def as_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "short_float_pct": self.short_float_pct,
            "short_ratio":     self.short_ratio,
            "float_shares_m":  self.float_shares_m,
            "inst_own_pct":    self.inst_own_pct,
            "squeeze_score":   self.squeeze_score,
            "squeeze_signal":  self.squeeze_signal,
            "summary":         self.summary,
        }


# ---------------------------------------------------------------------------
# Finviz parser
# ---------------------------------------------------------------------------

_FINVIZ_FIELDS = {
    "short_float_pct": r"Short Float.*?<td[^>]*>([\d.]+)%</td>",
    "short_ratio":     r"Short Ratio.*?<td[^>]*>([\d.]+)</td>",
    "float_shares":    r"Shs Float.*?<td[^>]*>([\d.]+[MBK]?)</td>",
    "inst_own_pct":    r"Inst Own.*?<td[^>]*>([\d.]+)%</td>",
}


def _parse_number(s: str) -> float | None:
    if not s:
        return None
    s = s.strip()
    multiplier = 1.0
    if s.endswith("B"):
        multiplier = 1000.0
        s = s[:-1]
    elif s.endswith("M"):
        multiplier = 1.0
        s = s[:-1]
    elif s.endswith("K"):
        multiplier = 0.001
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return None


async def _fetch_finviz(symbol: str, client: httpx.AsyncClient) -> dict:
    """Fetch and parse Finviz quote page for short interest data."""
    url = f"https://finviz.com/quote.ashx?t={symbol}&ty=c&ta=1&p=d"
    try:
        resp = await client.get(url, headers=_FINVIZ_HEADERS, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return {}
        html = resp.text

        result: dict = {}

        # Short Float %
        m = re.search(r"Short Float[^<]*</td>\s*<td[^>]*>([\d.,]+)%", html, re.IGNORECASE)
        if m:
            result["short_float_pct"] = _parse_number(m.group(1).replace(",", ""))

        # Short Ratio (days to cover)
        m = re.search(r"Short Ratio[^<]*</td>\s*<td[^>]*>([\d.,]+)", html, re.IGNORECASE)
        if m:
            result["short_ratio"] = _parse_number(m.group(1).replace(",", ""))

        # Float
        m = re.search(r"Shs Float[^<]*</td>\s*<td[^>]*>([\d.,]+[MBK]?)", html, re.IGNORECASE)
        if m:
            result["float_shares_m"] = _parse_number(m.group(1).replace(",", ""))

        # Institutional Ownership
        m = re.search(r"Inst Own[^<]*</td>\s*<td[^>]*>([\d.,]+)%", html, re.IGNORECASE)
        if m:
            result["inst_own_pct"] = _parse_number(m.group(1).replace(",", ""))

        return result

    except Exception as exc:
        logger.warning("Finviz fetch failed for %s: %s", symbol, exc)
        return {}


# ---------------------------------------------------------------------------
# Squeeze scoring
# ---------------------------------------------------------------------------

def _score_squeeze(
    short_float_pct: float | None,
    short_ratio:     float | None,
    float_shares_m:  float | None,
) -> tuple[float, str]:
    """
    Compute squeeze potential score (0-1) and label.

    Scoring factors:
      Short Float %   : > 30% = extreme; 20-30% = high; 10-20% = moderate
      Days to Cover   : > 8 = severe; 5-8 = significant; 2-5 = moderate
      Float size      : small float (<20M) + high SI = explosive potential
    """
    score = 0.0

    # Short float component (0-0.50)
    sf = short_float_pct or 0.0
    if sf >= 30:
        score += 0.50
    elif sf >= 20:
        score += 0.35
    elif sf >= 10:
        score += 0.20
    elif sf >= 5:
        score += 0.08

    # Days to cover component (0-0.35)
    dtc = short_ratio or 0.0
    if dtc >= 10:
        score += 0.35
    elif dtc >= 8:
        score += 0.28
    elif dtc >= 5:
        score += 0.20
    elif dtc >= 2:
        score += 0.10

    # Small float bonus (0-0.15)
    fl = float_shares_m or 999.0
    if fl < 10:
        score += 0.15
    elif fl < 20:
        score += 0.10
    elif fl < 50:
        score += 0.05

    score = round(min(score, 1.0), 4)

    if score >= 0.70:
        label = "extreme"
    elif score >= 0.45:
        label = "high"
    elif score >= 0.20:
        label = "moderate"
    else:
        label = "low"

    return score, label


def _build_intel(symbol: str, data: dict) -> ShortIntel:
    sf  = data.get("short_float_pct")
    dtc = data.get("short_ratio")
    fl  = data.get("float_shares_m")
    io  = data.get("inst_own_pct")

    score, label = _score_squeeze(sf, dtc, fl)

    lines: list[str] = []
    if sf is not None:
        lines.append(f"Short float: {sf:.1f}%")
    if dtc is not None:
        lines.append(f"Days to cover: {dtc:.1f}")
    if fl is not None:
        lines.append(f"Float: {fl:.0f}M shares")
    if io is not None:
        lines.append(f"Institutional ownership: {io:.0f}%")

    if label == "extreme":
        lines.append("EXTREME squeeze potential — any catalyst could trigger violent short covering.")
    elif label == "high":
        lines.append("HIGH squeeze potential — strong short interest; monitor for catalyst.")
    elif label == "moderate":
        lines.append("Moderate short interest — some squeeze potential with a catalyst.")

    summary = " | ".join(lines) if lines else f"No short interest data for {symbol}."

    return ShortIntel(
        symbol          = symbol,
        short_float_pct = sf,
        short_ratio     = dtc,
        float_shares_m  = fl,
        inst_own_pct    = io,
        squeeze_score   = score,
        squeeze_signal  = label,
        summary         = summary,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_short_interest_intel(watchlist: list[str]) -> dict[str, ShortIntel]:
    """
    Fetch short interest data for all watchlist symbols from Finviz.
    Returns dict of symbol → ShortIntel. Cached 4 hours.
    """
    missing = [s for s in watchlist if not _cache_fresh(s)]
    if not missing:
        return {s: _cache[s] for s in watchlist if s in _cache}

    # Fetch in small batches to avoid rate limiting
    async with httpx.AsyncClient() as client:
        for i in range(0, len(missing), 5):
            batch = missing[i:i+5]
            results = await asyncio.gather(
                *[_fetch_finviz(sym, client) for sym in batch],
                return_exceptions=True,
            )
            for sym, result in zip(batch, results):
                data = result if isinstance(result, dict) else {}
                intel = _build_intel(sym, data)
                _cache[sym] = intel
                _cache_built_at[sym] = datetime.now(timezone.utc)
            if i + 5 < len(missing):
                await asyncio.sleep(2.0)  # gentle rate limit

    return {s: _cache[s] for s in watchlist if s in _cache}


def build_short_interest_prompt_section(intel: dict[str, ShortIntel], symbol: str) -> str:
    """Format short interest data for Claude prompt injection."""
    info = intel.get(symbol)
    if info is None:
        return f"SHORT INTEREST [{symbol}]: No data available."
    if info.squeeze_signal == "low":
        sf = f"{info.short_float_pct:.1f}%" if info.short_float_pct else "N/A"
        return f"SHORT INTEREST [{symbol}]: Low squeeze potential (short float: {sf})."
    return f"SHORT INTEREST [{symbol}]: {info.squeeze_signal.upper()} squeeze potential | {info.summary}"


def short_interest_confidence_boost(
    intel: dict[str, ShortIntel],
    symbol: str,
    signal_side: str,
) -> float:
    """
    Confidence adjustment based on short interest and squeeze potential.

    Long + extreme squeeze:   +0.07 (potential squeeze amplifies upside)
    Long + high squeeze:      +0.04
    Short + extreme squeeze:  -0.10 (dangerous to short a heavily shorted stock)
    Short + high squeeze:     -0.06
    Otherwise:                 0.00
    """
    info = intel.get(symbol)
    if info is None:
        return 0.0

    raw     = signal_side.lower()
    if raw not in ("buy", "long", "sell", "short"):
        return 0.0
    is_long = raw in ("buy", "long")
    sig     = info.squeeze_signal

    if sig == "extreme":
        return +0.07 if is_long else -0.10
    if sig == "high":
        return +0.04 if is_long else -0.06
    if sig == "moderate":
        return +0.02 if is_long else -0.03
    return 0.0
