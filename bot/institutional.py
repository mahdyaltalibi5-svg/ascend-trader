"""
Ascend Institutional Intelligence — Smart Money Tracking.

Parses SEC EDGAR 13F filings to see exactly what elite hedge funds
are buying, selling, and holding. If Citadel and Millennium are both
loading NVDA — that's institutional conviction. We want to trade with them.

Data source: SEC EDGAR (100% free, no API key required).
13F filings are mandatory for funds with $100M+ AUM. Updated quarterly.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import httpx

# ---------------------------------------------------------------------------
# SEC EDGAR endpoints
# ---------------------------------------------------------------------------
SEC_BASE     = "https://data.sec.gov"
EDGAR_BASE   = "https://www.sec.gov"
SEC_HEADERS  = {
    # SEC requires a descriptive User-Agent
    "User-Agent": "AscendTrader research@ascendtrader.ai",
    "Accept":     "application/json",
    "Accept-Encoding": "gzip, deflate",
}

# ---------------------------------------------------------------------------
# Top hedge funds — verified SEC CIK numbers
# These are the smartest allocators on earth. We track all of them.
# ---------------------------------------------------------------------------
TOP_FUNDS: dict[str, str] = {
    "Citadel Advisors":          "0001423220",
    "Millennium Management":     "0001273931",
    "Two Sigma Investments":     "0001403570",
    "Point72 Asset Management":  "0001603466",
    "Coatue Management":         "0001336797",
    "Tiger Global Management":   "0001167483",
    "Dragoneer Investment":      "0001491778",
    "D.E. Shaw":                 "0001009207",
    "Whale Rock Capital":        "0001516078",
    "Viking Global":             "0001001085",
}

# ---------------------------------------------------------------------------
# Watchlist name mapping — used to match 13F holdings to our symbols.
# 13F reports company names (not tickers), so we fuzzy-match on these.
# ---------------------------------------------------------------------------
WATCHLIST_NAMES: dict[str, list[str]] = {
    "NVDA":  ["NVIDIA"],
    "TSLA":  ["TESLA"],
    "AAPL":  ["APPLE"],
    "MSFT":  ["MICROSOFT"],
    "META":  ["META PLATFORMS", "FACEBOOK"],
    "AMZN":  ["AMAZON"],
    "GOOGL": ["ALPHABET", "GOOGLE"],
    "AMD":   ["ADVANCED MICRO DEVICES"],
    "PLTR":  ["PALANTIR"],
    "SMCI":  ["SUPER MICRO COMPUTER", "SUPERMICRO"],
    "ARM":   ["ARM HOLDINGS"],
    "CRWD":  ["CROWDSTRIKE"],
    "PANW":  ["PALO ALTO NETWORKS"],
    "SOFI":  ["SOFI TECHNOLOGIES"],
    "COIN":  ["COINBASE"],
    "HOOD":  ["ROBINHOOD"],
    "MSTR":  ["MICROSTRATEGY"],
    "MARA":  ["MARATHON DIGITAL"],
    "RIOT":  ["RIOT PLATFORMS"],
    "IONQ":  ["IONQ"],
    "RKLB":  ["ROCKET LAB"],
    "RXRX":  ["RECURSION PHARMACEUTICALS"],
    "ACHR":  ["ARCHER AVIATION"],
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class Holding:
    name:   str
    cusip:  str
    value:  int    # USD thousands
    shares: int
    symbol: Optional[str] = None   # matched ticker


@dataclass
class FundSnapshot:
    fund_name:    str
    cik:          str
    filed_at:     str   # ISO date
    holdings:     list[Holding] = field(default_factory=list)
    period:       str = ""


@dataclass
class InstitutionalIntel:
    symbol:              str
    funds_holding:       int
    total_value_mm:      float     # total institutional value $M
    recent_accumulators: list[str]  # fund names increasing position
    recent_reducers:     list[str]  # fund names decreasing position
    conviction_score:    float      # 0-1
    signal:              str        # "accumulating" | "distributing" | "neutral"
    detail:              str        # human-readable summary

# ---------------------------------------------------------------------------
# EDGAR helpers
# ---------------------------------------------------------------------------
async def _get(client: httpx.AsyncClient, url: str, as_text: bool = False):
    r = await client.get(url, headers=SEC_HEADERS)
    r.raise_for_status()
    return r.text if as_text else r.json()


async def fetch_recent_13f_urls(cik: str, n: int = 2) -> list[tuple[str, str]]:
    """
    Return the N most recent 13F-HR filing accession numbers + dates for a fund.
    Returns list of (accession_number, filed_at).
    """
    url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    async with httpx.AsyncClient(timeout=20) as client:
        data = await _get(client, url)

    filings = data.get("filings", {}).get("recent", {})
    forms    = filings.get("form", [])
    accnos   = filings.get("accessionNumber", [])
    dates    = filings.get("filingDate", [])

    results = []
    for form, accno, date in zip(forms, accnos, dates):
        if form in ("13F-HR", "13F-HR/A"):
            results.append((accno.replace("-", ""), date))
        if len(results) >= n:
            break

    return results


async def fetch_13f_xml_url(cik: str, accno: str) -> Optional[str]:
    """Find the primary XML document URL inside a 13F filing."""
    # The archive directory uses the accession number without dashes. The
    # machine-readable directory listing is always named index.json.
    archive_base = f"{EDGAR_BASE}/Archives/edgar/data/{int(cik)}/{accno}"
    index_url = f"{archive_base}/index.json"
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            data = await _get(client, index_url)
        except Exception:
            return None

    for doc in data.get("directory", {}).get("item", []):
        name = doc.get("name", "")
        name_lower = name.lower()
        if name_lower.endswith(".xml") and "primary_doc" not in name_lower:
            # The holdings XML is usually named like "informationTable.xml"
            if any(kw in name_lower for kw in ["information", "infotable", "holdings"]):
                return f"{archive_base}/{name}"

    # Fallback: scan index for any XML that isn't the submission form
    for doc in data.get("directory", {}).get("item", []):
        name = doc.get("name", "")
        name_lower = name.lower()
        if name_lower.endswith(".xml") and "xbrl" not in name_lower and "primary_doc" not in name_lower:
            return f"{archive_base}/{name}"

    return None


def _parse_holdings_xml(xml_text: str) -> list[Holding]:
    """Parse 13F informationTable XML into Holding objects."""
    holdings = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def child_text(entry: ET.Element, tag: str) -> str:
        tag_lower = tag.lower()
        for el in entry.iter():
            if local_name(el.tag) == tag_lower and el.text:
                return el.text.strip()
        return ""

    def shares_val(entry: ET.Element) -> int:
        raw = child_text(entry, "sshPrnamt")
        try:
            return int(raw.replace(",", "")) if raw else 0
        except Exception:
            return 0

    entries = [entry for entry in root.iter() if local_name(entry.tag) == "infotable"]
    if not entries:
        entries = [entry for entry in root.iter() if local_name(entry.tag) == "information_table_entry"]

    for entry in entries:
        name = child_text(entry, "nameOfIssuer")
        cusip = child_text(entry, "cusip")
        try:
            value = int(child_text(entry, "value").replace(",", "") or "0")
        except Exception:
            value = 0

        if name and cusip and value > 0:
            holdings.append(Holding(name=name.upper(), cusip=cusip, value=value, shares=shares_val(entry)))

    return holdings


def _match_symbol(holding_name: str) -> Optional[str]:
    """Match a 13F company name to our watchlist ticker."""
    name_upper = holding_name.upper()
    for symbol, patterns in WATCHLIST_NAMES.items():
        for pattern in patterns:
            if pattern in name_upper:
                return symbol
    return None


async def fetch_fund_snapshot(fund_name: str, cik: str, filing_index: int = 0) -> Optional[FundSnapshot]:
    """
    Download and parse the most recent (or Nth most recent) 13F for one fund.
    filing_index=0 → latest, filing_index=1 → previous quarter.
    """
    try:
        filings = await fetch_recent_13f_urls(cik, n=2)
        if filing_index >= len(filings):
            return None

        accno, filed_at = filings[filing_index]
        xml_url = await fetch_13f_xml_url(cik, accno)

        if not xml_url:
            return None

        async with httpx.AsyncClient(timeout=30) as client:
            xml_text = await _get(client, xml_url, as_text=True)

        holdings = _parse_holdings_xml(xml_text)

        # Match to watchlist
        for h in holdings:
            h.symbol = _match_symbol(h.name)

        snapshot = FundSnapshot(
            fund_name=fund_name,
            cik=cik,
            filed_at=filed_at,
            holdings=holdings,
            period=filed_at[:7],
        )
        return snapshot

    except Exception:
        return None

# ---------------------------------------------------------------------------
# Core intelligence builder
# ---------------------------------------------------------------------------
async def get_smart_money_intel(
    watchlist: list[str],
    funds: dict[str, str] = TOP_FUNDS,
    max_age_days: int = 120,
) -> dict[str, InstitutionalIntel]:
    """
    Pull 13F data from all top funds concurrently.
    For each symbol in watchlist, compute:
      - How many top funds hold it
      - Total institutional value
      - Who's buying vs selling vs holding (comparing latest vs prior quarter)
      - Conviction score (0-1)
      - Signal: accumulating / distributing / neutral

    Returns dict[symbol → InstitutionalIntel].
    """

    # Fetch current and previous quarter snapshots for all funds concurrently
    tasks_current  = [fetch_fund_snapshot(name, cik, 0) for name, cik in funds.items()]
    tasks_previous = [fetch_fund_snapshot(name, cik, 1) for name, cik in funds.items()]

    current_snaps, previous_snaps = await asyncio.gather(
        asyncio.gather(*tasks_current,  return_exceptions=True),
        asyncio.gather(*tasks_previous, return_exceptions=True),
    )

    fund_names = list(funds.keys())

    # Build lookup: fund_name → {symbol → value_thousands}
    def build_lookup(snaps) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for name, snap in zip(fund_names, snaps):
            if isinstance(snap, FundSnapshot):
                sym_map: dict[str, int] = {}
                for h in snap.holdings:
                    if h.symbol:
                        sym_map[h.symbol] = sym_map.get(h.symbol, 0) + h.value
                result[name] = sym_map
        return result

    curr_lookup = build_lookup(current_snaps)
    prev_lookup = build_lookup(previous_snaps)

    intel: dict[str, InstitutionalIntel] = {}

    for symbol in watchlist:
        funds_holding       = 0
        total_value_mm      = 0.0
        accumulators: list[str] = []
        reducers: list[str]     = []

        for fund_name in fund_names:
            curr_val = curr_lookup.get(fund_name, {}).get(symbol, 0)
            prev_val = prev_lookup.get(fund_name, {}).get(symbol, 0)

            if curr_val > 0:
                funds_holding += 1
                total_value_mm += curr_val / 1000  # thousands → millions

            # Change analysis
            if curr_val > 0 and prev_val == 0:
                accumulators.append(f"{fund_name} (NEW)")
            elif curr_val > prev_val * 1.15:   # >15% increase
                pct = int((curr_val - prev_val) / prev_val * 100)
                accumulators.append(f"{fund_name} (+{pct}%)")
            elif prev_val > 0 and curr_val == 0:
                reducers.append(f"{fund_name} (CLOSED)")
            elif prev_val > 0 and curr_val < prev_val * 0.85:
                pct = int((prev_val - curr_val) / prev_val * 100)
                reducers.append(f"{fund_name} (-{pct}%)")

        # Conviction score: combination of fund count + accumulation trend
        max_funds    = len(fund_names)
        fund_score   = funds_holding / max_funds          # 0-1
        accum_score  = min(len(accumulators) / 3, 1.0)   # 0-1 (3 accumulators = max)
        reduce_score = min(len(reducers) / 3, 1.0)       # penalty
        conviction   = round(fund_score * 0.5 + accum_score * 0.4 - reduce_score * 0.3, 3)
        conviction   = max(0.0, min(1.0, conviction))

        # Signal
        if len(accumulators) >= 2 and len(accumulators) > len(reducers):
            signal = "accumulating"
        elif len(reducers) >= 2 and len(reducers) > len(accumulators):
            signal = "distributing"
        else:
            signal = "neutral"

        # Summary
        if funds_holding == 0:
            detail = f"No top-fund coverage for {symbol}."
        else:
            detail = (
                f"{funds_holding} top funds hold {symbol} (${total_value_mm:.0f}M total). "
                + (f"BUYING: {', '.join(accumulators[:2])}. " if accumulators else "")
                + (f"SELLING: {', '.join(reducers[:2])}." if reducers else "")
            )

        intel[symbol] = InstitutionalIntel(
            symbol=symbol,
            funds_holding=funds_holding,
            total_value_mm=round(total_value_mm, 1),
            recent_accumulators=accumulators,
            recent_reducers=reducers,
            conviction_score=conviction,
            signal=signal,
            detail=detail,
        )

    return intel


def build_institutional_context(intel: dict[str, InstitutionalIntel], symbol: str) -> str:
    """
    Format institutional intelligence as a Claude prompt section.
    Call this inside analyze_with_claude() to add smart money context.
    """
    if symbol not in intel:
        return "No institutional data available."

    info = intel[symbol]

    if info.funds_holding == 0:
        return f"INSTITUTIONAL POSITIONING: No major fund coverage. Retail-driven name — higher risk."

    lines = [
        f"INSTITUTIONAL POSITIONING ({info.signal.upper()}, 13F LAGGED QUARTERLY DATA):",
        f"  Funds holding: {info.funds_holding}/{len(TOP_FUNDS)} elite funds | Total value: ${info.total_value_mm:.0f}M",
        f"  Conviction score: {info.conviction_score:.2f}/1.0",
    ]
    if info.recent_accumulators:
        lines.append(f"  ACCUMULATING (last quarter): {', '.join(info.recent_accumulators[:3])}")
    if info.recent_reducers:
        lines.append(f"  REDUCING (last quarter): {', '.join(info.recent_reducers[:3])}")

    guidance = {
        "accumulating": "Recent 13F filings show increased ownership. Treat as a slow institutional tailwind, not an intraday catalyst.",
        "distributing": "Recent 13F filings show reduced ownership. Treat as a slow institutional headwind, not a real-time sell signal.",
        "neutral":      "Institutional positioning is stable. Neutral factor.",
    }
    lines.append(f"  Interpretation: {guidance.get(info.signal, '')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoring helper — call this in composite_score() in main.py
# ---------------------------------------------------------------------------
def institutional_signal_boost(intel: dict[str, InstitutionalIntel], symbol: str, signal_side: str) -> float:
    """
    Returns a score bonus/penalty to apply to composite_score.
    +0.10 if smart money accumulating and we're buying
    +0.10 if smart money distributing and we're selling (short)
    -0.15 if we're trading AGAINST institutional flow
    """
    if symbol not in intel:
        return 0.0

    info = intel[symbol]
    side = signal_side.lower()
    strength = max(0.25, min(1.0, info.conviction_score))

    if info.signal == "accumulating" and side == "buy":
        return 0.10 * strength   # trading WITH the smart money
    if info.signal == "distributing" and side == "sell":
        return 0.08 * strength   # shorting what filings show they reduced
    if info.signal == "accumulating" and side == "sell":
        return -0.15 * strength  # shorting against institutional accumulation
    if info.signal == "distributing" and side == "buy":
        return -0.12 * strength  # buying into institutional reduction

    return 0.0


# ---------------------------------------------------------------------------
# Cache — 13F updates quarterly, no need to re-fetch every scan
# ---------------------------------------------------------------------------
_intel_cache: dict[str, InstitutionalIntel] = {}
_cache_built_at: Optional[datetime] = None
CACHE_TTL_HOURS = 24   # refresh daily (filings don't change that fast)


async def get_cached_intel(watchlist: list[str]) -> dict[str, InstitutionalIntel]:
    """Return cached intel if fresh, otherwise rebuild from SEC EDGAR."""
    global _intel_cache, _cache_built_at

    now = datetime.now(timezone.utc)
    cache_stale = (
        _cache_built_at is None or
        (now - _cache_built_at).total_seconds() > CACHE_TTL_HOURS * 3600
    )

    if cache_stale:
        try:
            _intel_cache   = await get_smart_money_intel(watchlist)
            _cache_built_at = now
        except Exception:
            pass  # return whatever we have

    return _intel_cache
