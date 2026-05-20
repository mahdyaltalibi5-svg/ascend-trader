"""
Options amplify returns. A 5% stock move can produce 50-200% gain on options.
This module handles options execution for high-confidence signals.

Alpaca supports options trading via the /v2/options/contracts endpoint. This
module fetches option chains, selects optimal near-money contracts (delta
0.35–0.55), sizes positions to risk no more than 2% of portfolio per trade,
and places orders through the Alpaca paper/live API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums and data types
# ---------------------------------------------------------------------------


class OptionType(str, Enum):
    CALL = "call"
    PUT  = "put"


@dataclass
class OptionContract:
    symbol:      str           # OCC option symbol, e.g. "AAPL240119C00185000"
    underlying:  str           # Underlying ticker, e.g. "AAPL"
    expiry:      date          # Expiration date
    strike:      float         # Strike price
    option_type: OptionType    # CALL or PUT
    bid:         float         # Current bid
    ask:         float         # Current ask
    iv:          Optional[float] = None   # Implied volatility (0-1)
    delta:       Optional[float] = None   # Option delta (0–1 for calls, -1–0 for puts)
    gamma:       Optional[float] = None   # Option gamma

    @property
    def mid(self) -> float:
        """Mid-market premium."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as a percentage of mid. Lower is more liquid."""
        if self.mid <= 0:
            return 1.0
        return (self.ask - self.bid) / self.mid

    @property
    def abs_delta(self) -> float:
        """Absolute delta (always positive, regardless of put/call)."""
        return abs(self.delta) if self.delta is not None else 0.0


# ---------------------------------------------------------------------------
# Fetch option chain from Alpaca
# ---------------------------------------------------------------------------

ALPACA_OPTIONS_URL = "https://paper-api.alpaca.markets/v2/options/contracts"


async def fetch_option_chain(
    underlying: str,
    alpaca_headers: dict[str, str],
    expiry_days: int = 7,
) -> list[OptionContract]:
    """
    Fetch option contracts for *underlying* expiring within *expiry_days*
    from Alpaca paper API.

    Parameters
    ----------
    underlying     : Ticker symbol, e.g. "NVDA"
    alpaca_headers : Dict with APCA-API-KEY-ID and APCA-API-SECRET-KEY
    expiry_days    : Look for contracts expiring within this many days

    Returns
    -------
    List of OptionContract objects (empty list on error or no data).
    """
    today      = date.today()
    expiry_end = today + timedelta(days=expiry_days)

    params = {
        "underlying_symbols": underlying.upper(),
        "expiration_date_gte": today.isoformat(),
        "expiration_date_lte": expiry_end.isoformat(),
        "feed": "indicative",
        "limit": 200,
    }

    contracts: list[OptionContract] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                ALPACA_OPTIONS_URL,
                headers=alpaca_headers,
                params=params,
            )
            if resp.status_code != 200:
                logger.warning(
                    "fetch_option_chain: Alpaca returned %d for %s",
                    resp.status_code,
                    underlying,
                )
                return contracts

            payload = resp.json()
            items = payload.get("option_contracts", []) or payload.get("contracts", [])
            for item in items:
                try:
                    raw_type = str(item.get("type", "call")).lower()
                    opt_type = OptionType.CALL if raw_type == "call" else OptionType.PUT

                    raw_expiry = item.get("expiration_date", "")
                    exp_date = date.fromisoformat(str(raw_expiry)[:10])

                    bid    = float(item.get("bid_price", 0) or 0)
                    ask    = float(item.get("ask_price", 0) or 0)
                    strike = float(item.get("strike_price", 0) or 0)

                    greeks = item.get("greeks") or {}
                    delta  = float(greeks["delta"]) if greeks.get("delta") is not None else None
                    gamma  = float(greeks["gamma"]) if greeks.get("gamma") is not None else None
                    iv     = float(greeks["iv"])    if greeks.get("iv")    is not None else None

                    # Fallback: greeks at top level (some Alpaca responses)
                    if delta is None and item.get("delta") is not None:
                        delta = float(item["delta"])
                    if gamma is None and item.get("gamma") is not None:
                        gamma = float(item["gamma"])
                    if iv is None and item.get("implied_volatility") is not None:
                        iv = float(item["implied_volatility"])

                    contracts.append(
                        OptionContract(
                            symbol=str(item.get("symbol", "")),
                            underlying=underlying.upper(),
                            expiry=exp_date,
                            strike=strike,
                            option_type=opt_type,
                            bid=bid,
                            ask=ask,
                            iv=iv,
                            delta=delta,
                            gamma=gamma,
                        )
                    )
                except (KeyError, ValueError, TypeError) as exc:
                    logger.debug("Skipping malformed contract: %s", exc)

    except httpx.HTTPError as exc:
        logger.warning("fetch_option_chain HTTP error for %s: %s", underlying, exc)

    return contracts


# ---------------------------------------------------------------------------
# Contract selection
# ---------------------------------------------------------------------------

# Target delta range: near-money contracts
DELTA_LOW  = 0.35
DELTA_HIGH = 0.55

# Maximum bid-ask spread as fraction of mid (5% = liquid enough)
MAX_SPREAD_PCT = 0.05


def select_optimal_contract(
    contracts: list[OptionContract],
    signal_side: str,
    current_price: float,
    confidence: float,
) -> Optional[OptionContract]:
    """
    Pick the best option contract for a given signal.

    Logic:
    - BUY signals → CALL options; SELL signals → PUT options
    - Filter for delta in [0.35, 0.55] (near-money; not too far OTM/ITM)
    - Filter for bid-ask spread < 5% of mid (liquid contracts only)
    - Sort survivors by proximity of |delta| to 0.45 (sweet spot),
      then by lowest spread for tie-breaking

    Parameters
    ----------
    contracts    : Full option chain from fetch_option_chain()
    signal_side  : "buy" or "sell"
    current_price: Current underlying price (used for additional context)
    confidence   : Signal confidence (0–1); not used for filtering but
                   reserved for future dynamic delta targeting

    Returns None if no contract meets the criteria.
    """
    side_lower = signal_side.lower()
    target_type = OptionType.CALL if side_lower == "buy" else OptionType.PUT

    candidates = [
        c for c in contracts
        if c.option_type == target_type
        and c.delta is not None
        and DELTA_LOW <= c.abs_delta <= DELTA_HIGH
        and c.mid > 0.01            # non-zero premium
        and c.spread_pct < MAX_SPREAD_PCT
    ]

    if not candidates:
        logger.info(
            "select_optimal_contract: No contracts pass filters for %s signal "
            "(type=%s, delta %.2f–%.2f, spread<%.0f%%)",
            side_lower,
            target_type.value,
            DELTA_LOW,
            DELTA_HIGH,
            MAX_SPREAD_PCT * 100,
        )
        return None

    # Primary sort: closest delta to 0.45 (near-money sweet spot)
    # Secondary: smallest spread (most liquid)
    DELTA_TARGET = 0.45
    candidates.sort(key=lambda c: (abs(c.abs_delta - DELTA_TARGET), c.spread_pct))

    return candidates[0]


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


def calculate_option_position_size(
    portfolio_value: float,
    premium: float,
    max_risk_pct: float = 0.02,
) -> int:
    """
    Calculate how many option contracts to buy, risking no more than
    *max_risk_pct* of portfolio value.

    Each contract = 100 shares. Total risk = qty × premium × 100.

    Parameters
    ----------
    portfolio_value : Total portfolio equity in dollars
    premium         : Option premium (mid price per share)
    max_risk_pct    : Maximum portfolio fraction to risk (default 2%)

    Returns
    -------
    Number of contracts (minimum 1 if budget allows, otherwise 0).
    """
    if premium <= 0 or portfolio_value <= 0:
        return 0

    max_risk_dollars = portfolio_value * max_risk_pct
    cost_per_contract = premium * 100  # 1 contract = 100 shares

    if cost_per_contract <= 0:
        return 0

    qty = int(max_risk_dollars / cost_per_contract)
    return max(0, qty)


# ---------------------------------------------------------------------------
# Order building and placement
# ---------------------------------------------------------------------------


def build_option_order(
    contract: OptionContract,
    qty: int,
    side: str,
) -> dict:
    """
    Build an Alpaca order payload for an options order.

    Parameters
    ----------
    contract : The OptionContract to trade
    qty      : Number of contracts (each = 100 shares)
    side     : "buy" to open, "sell" to close

    Returns
    -------
    Dict ready to POST to Alpaca /v2/orders.
    """
    return {
        "symbol":        contract.symbol,
        "qty":           str(qty),
        "side":          side.lower(),
        "type":          "market",
        "time_in_force": "day",
        # Options orders do not use order_class=bracket; manage exits manually
    }


async def place_option_order(
    contract: OptionContract,
    qty: int,
    alpaca_trade_url: str,
    alpaca_headers: dict[str, str],
) -> dict:
    """
    Place a market buy order for *qty* contracts of *contract* via Alpaca.

    Parameters
    ----------
    contract         : The OptionContract to buy
    qty              : Number of contracts
    alpaca_trade_url : Base URL, e.g. "https://paper-api.alpaca.markets"
    alpaca_headers   : Dict with APCA-API-KEY-ID and APCA-API-SECRET-KEY

    Returns
    -------
    Alpaca order response dict. Raises httpx.HTTPStatusError on failure.
    """
    if qty <= 0:
        return {"error": "qty must be >= 1", "qty": qty}

    payload = build_option_order(contract, qty, "buy")
    url = f"{alpaca_trade_url.rstrip('/')}/v2/orders"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload, headers=alpaca_headers)
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "Option order placed: %s x%d | symbol=%s | id=%s",
            contract.underlying,
            qty,
            contract.symbol,
            result.get("id", "?"),
        )
        return result


# ---------------------------------------------------------------------------
# Profit target estimation
# ---------------------------------------------------------------------------


def estimate_profit_target(
    contract: OptionContract,
    entry_premium: float,
    rr: float = 2.5,
) -> float:
    """
    Calculate target exit premium for a given risk/reward ratio.

    For options bought (max loss = entry premium), the target exit premium
    that achieves *rr* R is simply entry_premium × (1 + rr).

    Example: entry at $1.00, rr=2.5 → target $3.50 (250% gain).

    Parameters
    ----------
    contract      : The OptionContract (used for context/logging)
    entry_premium : Per-share premium paid at entry
    rr            : Desired risk/reward multiple (default 2.5)

    Returns
    -------
    Target premium per share (float). Round to 2 decimal places.
    """
    if entry_premium <= 0:
        return 0.0

    target = entry_premium * (1.0 + rr)
    return round(target, 2)
