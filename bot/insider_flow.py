"""
insider_flow.py — SEC Form 4 Insider Trade Tracker for Ascend Trader Elite

Fetches and parses recent Form 4 filings from SEC EDGAR to detect meaningful
open-market insider buying. Insider buying is one of the most reliable
leading indicators of positive company fundamentals — insiders rarely buy
their own stock unless they expect it to rise.

Rules for what counts as a meaningful buy:
  - Transaction type = "P" (open-market purchase) only.
    Ignores: "A" (award), "M" (option exercise), "F" (tax withholding),
             "S" (sale), "G" (gift), "D" (disposition to issuer).
  - Insider role score: CEO/CFO = high (1.0), Director/COO = medium (0.7),
    VP/other officer = low (0.4).
  - Minimum dollar value: $50,000 per transaction.
  - Score strengthens when multiple insiders buy within 30 days.

Data source: SEC EDGAR EDGAR full-text search + company facts API.
Rate limiting: 10 req/sec max per SEC guidelines; we stay well under with caching.
Cache TTL: 4 hours (Form 4s are filed within 2 business days of transaction).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SEC EDGAR config
# ---------------------------------------------------------------------------

SEC_BASE      = "https://efts.sec.gov"
SEC_API_BASE  = "https://data.sec.gov"
SEC_HEADERS   = {
    "User-Agent": "AscendTrader research@ascend.trade",
    "Accept":     "application/json",
}

# Transaction codes that represent genuine open-market purchases
PURCHASE_CODES = {"P"}

# Role keywords and their conviction weights
_ROLE_WEIGHTS: list[tuple[tuple[str, ...], float]] = [
    (("chief executive", "ceo"),            1.00),
    (("chief financial", "cfo"),            1.00),
    (("chief operating", "coo"),            0.85),
    (("president",),                        0.85),
    (("director",),                         0.70),
    (("chief technology", "cto"),           0.70),
    (("chief revenue",),                    0.70),
    (("executive vice president", "evp"),   0.60),
    (("senior vice president", "svp"),      0.50),
    (("vice president", "vp"),              0.40),
]

MIN_PURCHASE_USD   = 50_000   # ignore small purchases
MAX_FILING_AGE_DAYS = 60      # only consider filings within 60 days


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_cache: dict[str, "InsiderIntel"] = {}
_cache_built_at: datetime | None = None
_CACHE_TTL_HOURS = 4


def _cache_fresh() -> bool:
    if _cache_built_at is None:
        return False
    return (datetime.now(timezone.utc) - _cache_built_at) < timedelta(hours=_CACHE_TTL_HOURS)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class InsiderTransaction:
    insider_name:  str
    insider_title: str
    role_weight:   float    # 0-1 conviction weight based on role
    shares:        int
    price_per_share: float
    total_value:   float    # shares * price
    transaction_date: date
    filing_date:   date
    form_url:      str


@dataclass
class InsiderIntel:
    symbol:      str
    has_buying:  bool
    signal:      str    # "strong_buy" | "buy" | "neutral" | "distribution"
    score:       float  # 0-1 composite insider conviction score
    transactions: list[InsiderTransaction] = field(default_factory=list)
    summary:     str = ""

    def as_dict(self) -> dict:
        return {
            "symbol":      self.symbol,
            "has_buying":  self.has_buying,
            "signal":      self.signal,
            "score":       self.score,
            "num_buys":    len([t for t in self.transactions if t.total_value > 0]),
            "total_bought_usd": sum(t.total_value for t in self.transactions),
            "summary":     self.summary,
        }


# ---------------------------------------------------------------------------
# CIK lookup
# ---------------------------------------------------------------------------

async def _get_cik(ticker: str, client: httpx.AsyncClient) -> str | None:
    """Resolve ticker → CIK using SEC company facts lookup."""
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=4"
        # Actually use the tickers.json lookup which is more reliable
        tickers_url = "https://www.sec.gov/files/company_tickers.json"
        resp = await client.get(tickers_url, headers=SEC_HEADERS, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                cik = str(entry["cik_str"]).zfill(10)
                return cik
        return None
    except Exception as exc:
        logger.warning("CIK lookup failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Form 4 filing parser
# ---------------------------------------------------------------------------

def _role_weight(title: str) -> float:
    """Map insider title to a conviction weight (0-1)."""
    title_lower = title.lower()
    for keywords, weight in _ROLE_WEIGHTS:
        if any(kw in title_lower for kw in keywords):
            return weight
    return 0.30  # unknown role


async def _fetch_recent_form4s(
    cik: str,
    client: httpx.AsyncClient,
    max_filings: int = 20,
) -> list[dict]:
    """
    Fetch the list of recent Form 4 filings for a CIK.

    Returns list of submission dicts from EDGAR submissions API.
    """
    url = f"{SEC_API_BASE}/submissions/CIK{cik}.json"
    try:
        resp = await client.get(url, headers=SEC_HEADERS, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms      = recent.get("form", [])
        acc_nums   = recent.get("accessionNumber", [])
        filed_dates = recent.get("filingDate", [])

        results: list[dict] = []
        for i, form in enumerate(forms):
            if form == "4":
                results.append({
                    "accession": acc_nums[i].replace("-", ""),
                    "filed":     filed_dates[i],
                })
                if len(results) >= max_filings:
                    break

        return results
    except Exception as exc:
        logger.warning("Form4 list fetch failed for CIK %s: %s", cik, exc)
        return []


async def _parse_form4_filing(
    cik: str,
    accession: str,
    filed_str: str,
    client: httpx.AsyncClient,
) -> list[InsiderTransaction]:
    """
    Download and parse the XML from a Form 4 filing accession.

    Returns list of InsiderTransaction (only open-market purchases).
    """
    # Build the primary doc URL
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession}/{accession[0:10]}-{accession[10:12]}-{accession[12:]}.txt"
    )
    # Simpler: hit the filing index and grab the first .xml doc
    index_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={cik}&type=4&dateb=&owner=include&count=40&search_text="
    )

    # Direct XML path is predictable for EDGAR
    xml_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession[:10]}-{accession[10:12]}-{accession[12:]}.txt"
    )

    # Try fetching the submission package
    try:
        pkg_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"
        resp = await client.get(pkg_url, headers={**SEC_HEADERS, "Accept": "text/html"}, timeout=15.0)
        # Look for .xml links in the index
        import re
        xml_files = re.findall(r'href="([^"]+\.xml)"', resp.text, re.IGNORECASE)
        if not xml_files:
            return []
        xml_href = xml_files[0]
        if not xml_href.startswith("http"):
            xml_href = f"https://www.sec.gov{xml_href}"

        xml_resp = await client.get(xml_href, headers=SEC_HEADERS, timeout=15.0)
        xml_resp.raise_for_status()
        return _parse_form4_xml(xml_resp.text, filed_str)

    except Exception as exc:
        logger.debug("Form4 XML parse failed for accession %s: %s", accession, exc)
        return []


def _parse_form4_xml(xml_text: str, filed_str: str) -> list[InsiderTransaction]:
    """
    Parse Form 4 XML and extract open-market purchases.

    The SEC Form 4 XML schema has these relevant elements:
      <reportingOwner>
        <reportingOwnerRelationship>
          <officerTitle>CEO</officerTitle>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <transactionCoding>
            <transactionCode>P</transactionCode>   ← open-market purchase
          <transactionAmounts>
            <transactionShares><value>5000</value>
            <transactionPricePerShare><value>42.50</value>
          <transactionDate><value>2026-05-15</value>
    """
    import re

    transactions: list[InsiderTransaction] = []

    try:
        filed_date = date.fromisoformat(filed_str[:10])
    except (ValueError, TypeError):
        filed_date = date.today()

    # Extract insider identity
    name_match  = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml_text, re.IGNORECASE | re.DOTALL)
    title_match = re.search(r"<officerTitle>(.*?)</officerTitle>", xml_text, re.IGNORECASE | re.DOTALL)
    is_director = bool(re.search(r"<isDirector>1</isDirector>", xml_text, re.IGNORECASE))

    insider_name  = _strip_xml(name_match.group(1)) if name_match else "Unknown"
    insider_title = _strip_xml(title_match.group(1)) if title_match else ("Director" if is_director else "Officer")
    role_wt       = _role_weight(insider_title)

    # Extract each nonDerivativeTransaction block
    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>",
        xml_text,
        re.DOTALL | re.IGNORECASE,
    )

    for block in blocks:
        try:
            code_m = re.search(r"<transactionCode>(.*?)</transactionCode>", block, re.IGNORECASE | re.DOTALL)
            code = _strip_xml(code_m.group(1)) if code_m else ""

            if code not in PURCHASE_CODES:
                continue  # ignore everything that is not an open-market purchase

            shares_m = re.search(
                r"<transactionShares>.*?<value>(.*?)</value>", block, re.DOTALL | re.IGNORECASE
            )
            price_m = re.search(
                r"<transactionPricePerShare>.*?<value>(.*?)</value>", block, re.DOTALL | re.IGNORECASE
            )
            date_m  = re.search(
                r"<transactionDate>.*?<value>(.*?)</value>", block, re.DOTALL | re.IGNORECASE
            )

            shares = float(_strip_xml(shares_m.group(1))) if shares_m else 0
            price  = float(_strip_xml(price_m.group(1))) if price_m else 0
            total  = shares * price

            if total < MIN_PURCHASE_USD:
                continue  # too small to be meaningful

            txn_date_str = _strip_xml(date_m.group(1)) if date_m else filed_str[:10]
            try:
                txn_date = date.fromisoformat(txn_date_str[:10])
            except (ValueError, TypeError):
                txn_date = filed_date

            # Ignore if too old
            age_days = (date.today() - txn_date).days
            if age_days > MAX_FILING_AGE_DAYS:
                continue

            transactions.append(InsiderTransaction(
                insider_name     = insider_name,
                insider_title    = insider_title,
                role_weight      = role_wt,
                shares           = int(shares),
                price_per_share  = price,
                total_value      = total,
                transaction_date = txn_date,
                filing_date      = filed_date,
                form_url         = "",
            ))

        except Exception:
            continue

    return transactions


def _strip_xml(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).strip()


# ---------------------------------------------------------------------------
# High-level intel builder
# ---------------------------------------------------------------------------

def _score_insider_intel(transactions: list[InsiderTransaction]) -> tuple[float, str, str]:
    """
    Compute a 0-1 insider conviction score and signal label.

    Factors:
    - Role weight of buyer (CEO buys matter more than VP buys)
    - Dollar amount (larger = more conviction)
    - Number of distinct insiders buying
    - Recency (buys this week > buys last month)
    """
    if not transactions:
        return 0.0, "neutral", "No insider buying detected in the past 60 days."

    today       = date.today()
    total_usd   = sum(t.total_value for t in transactions)
    unique_names = len({t.insider_name for t in transactions})
    max_role     = max(t.role_weight for t in transactions)

    # Recency boost: weight recent buys more heavily
    weighted_usd = 0.0
    for t in transactions:
        age_days = (today - t.transaction_date).days
        recency  = max(0.1, 1.0 - age_days / MAX_FILING_AGE_DAYS)
        weighted_usd += t.total_value * recency

    # Sub-scores (0-1 each)
    dollar_score   = min(weighted_usd / 2_000_000, 1.0)  # saturates at $2M
    breadth_score  = min(unique_names / 3.0, 1.0)        # saturates at 3 insiders
    role_score     = max_role

    score = (dollar_score * 0.40) + (breadth_score * 0.30) + (role_score * 0.30)
    score = round(min(score, 1.0), 4)

    if score >= 0.65:
        signal = "strong_buy"
    elif score >= 0.35:
        signal = "buy"
    else:
        signal = "neutral"

    # Human summary
    top_buyer = max(transactions, key=lambda t: t.total_value)
    summary = (
        f"{unique_names} insider(s) bought ${total_usd:,.0f} total in the past "
        f"{MAX_FILING_AGE_DAYS} days. "
        f"Largest: {top_buyer.insider_title} {top_buyer.insider_name} "
        f"({top_buyer.shares:,} shares @ ${top_buyer.price_per_share:.2f} = "
        f"${top_buyer.total_value:,.0f} on {top_buyer.transaction_date}). "
        f"Insider conviction score: {score:.2f}."
    )

    return score, signal, summary


async def _build_intel_for_symbol(
    ticker: str,
    client: httpx.AsyncClient,
) -> InsiderIntel:
    """Build InsiderIntel for one symbol."""
    cik = await _get_cik(ticker, client)
    if not cik:
        return InsiderIntel(
            symbol=ticker, has_buying=False, signal="neutral", score=0.0,
            summary=f"CIK not found for {ticker}.",
        )

    filings = await _fetch_recent_form4s(cik, client, max_filings=15)
    if not filings:
        return InsiderIntel(
            symbol=ticker, has_buying=False, signal="neutral", score=0.0,
            summary=f"No Form 4 filings found for {ticker}.",
        )

    # Parse filings concurrently (max 5 at a time to respect SEC rate limits)
    all_transactions: list[InsiderTransaction] = []
    for i in range(0, len(filings), 5):
        batch = filings[i:i+5]
        results = await asyncio.gather(
            *[_parse_form4_filing(cik, f["accession"], f["filed"], client) for f in batch],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                all_transactions.extend(result)
        await asyncio.sleep(0.5)  # gentle rate limiting

    purchases = [t for t in all_transactions]  # already filtered to purchase codes
    score, signal, summary = _score_insider_intel(purchases)

    return InsiderIntel(
        symbol       = ticker,
        has_buying   = len(purchases) > 0,
        signal       = signal,
        score        = score,
        transactions = purchases,
        summary      = summary,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_insider_intel(watchlist: list[str]) -> dict[str, InsiderIntel]:
    """
    Fetch Form 4 insider buying data for all symbols in watchlist.

    Results are cached for 4 hours. Returns dict of symbol → InsiderIntel.
    """
    global _cache, _cache_built_at

    if _cache_fresh() and all(s in _cache for s in watchlist):
        return _cache

    async with httpx.AsyncClient() as client:
        # Process in small batches to stay under SEC rate limits (10 req/sec)
        results: dict[str, InsiderIntel] = {}
        for i in range(0, len(watchlist), 3):
            batch = watchlist[i:i+3]
            batch_results = await asyncio.gather(
                *[_build_intel_for_symbol(sym, client) for sym in batch],
                return_exceptions=True,
            )
            for sym, result in zip(batch, batch_results):
                if isinstance(result, InsiderIntel):
                    results[sym] = result
                else:
                    results[sym] = InsiderIntel(
                        symbol=sym, has_buying=False, signal="neutral", score=0.0,
                        summary=f"Insider intel fetch failed: {result}",
                    )
            await asyncio.sleep(1.0)  # 1 second between batches

    _cache = results
    _cache_built_at = datetime.now(timezone.utc)
    logger.info("insider_flow: built intel for %d symbols", len(results))
    return results


def build_insider_prompt_section(intel: dict[str, InsiderIntel], symbol: str) -> str:
    """Format insider intel for Claude prompt injection."""
    info = intel.get(symbol)
    if info is None:
        return f"INSIDER FLOW [{symbol}]: No data available."

    if not info.has_buying or info.signal == "neutral":
        return f"INSIDER FLOW [{symbol}]: No meaningful insider buying in past 60 days."

    signal_label = {
        "strong_buy": "STRONG INSIDER BUYING",
        "buy":        "INSIDER BUYING DETECTED",
        "neutral":    "No significant insider activity",
    }.get(info.signal, "NEUTRAL")

    lines = [f"INSIDER FLOW [{symbol}]: {signal_label} — {info.summary}"]

    if info.transactions:
        lines.append("Recent transactions (open-market purchases only):")
        for t in sorted(info.transactions, key=lambda x: x.transaction_date, reverse=True)[:3]:
            lines.append(
                f"  • {t.transaction_date} | {t.insider_title}: "
                f"{t.shares:,} shares @ ${t.price_per_share:.2f} = ${t.total_value:,.0f}"
            )

    return "\n".join(lines)


def insider_confidence_boost(intel: dict[str, InsiderIntel], symbol: str, side: str) -> float:
    """
    Confidence adjustment based on insider flow.

    Long into strong buying: +0.06
    Long into weak buying:   +0.03
    Short into buying:       -0.08 (insiders know more than you)
    No signal:                0.00
    """
    info = intel.get(symbol)
    if info is None:
        return 0.0

    raw = side.lower()
    is_long = raw in ("buy", "long")

    if info.signal == "strong_buy":
        return +0.06 if is_long else -0.08
    if info.signal == "buy":
        return +0.03 if is_long else -0.05
    return 0.0
