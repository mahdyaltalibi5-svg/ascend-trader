"""
Ascend Trader Elite — Portfolio-Level Risk Management Engine

Provides sector concentration checks, correlated exposure guards, portfolio
heat limits, Kelly-based position sizing, setup quality scoring, and a
unified trade-validation gate that all signals must pass before execution.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Sector mappings
# ---------------------------------------------------------------------------

SECTOR_MAP: dict[str, str] = {
    # Semis
    "NVDA": "semis",
    "AMD":  "semis",
    "SMCI": "semis",
    "ARM":  "semis",
    # Mega-cap tech
    "AAPL":  "mega_tech",
    "MSFT":  "mega_tech",
    "META":  "mega_tech",
    "AMZN":  "mega_tech",
    "GOOGL": "mega_tech",
    # EV / auto
    "TSLA": "ev_auto",
    # Cybersecurity
    "CRWD": "cybersec",
    "PANW": "cybersec",
    # Crypto-adjacent / fintech
    "COIN": "crypto_fin",
    "HOOD": "crypto_fin",
    "MSTR": "crypto_fin",
    "MARA": "crypto_fin",
    "RIOT": "crypto_fin",
    "SOFI": "crypto_fin",
    # Enterprise software
    "PLTR": "enterprise_sw",
    # Volatile growth
    "IONQ": "growth_tech",
    "RKLB": "growth_tech",
    "RXRX": "growth_tech",
    "ACHR": "growth_tech",
}

# ---------------------------------------------------------------------------
# Risk constants
# ---------------------------------------------------------------------------

MAX_SECTOR_POSITIONS: int   = 2
MAX_PORTFOLIO_HEAT:   float = 0.12   # 12 % of portfolio at risk simultaneously

# ---------------------------------------------------------------------------
# Correlated groups
# Symbols in the same group tend to move together; limit exposure to 2 max.
# ---------------------------------------------------------------------------

CORRELATED_GROUPS: list[list[str]] = [
    # Crypto ecosystem — follow BTC / ETH tick-for-tick
    ["MARA", "RIOT", "MSTR", "COIN", "HOOD"],
    # Semiconductors — SOX-correlated
    ["NVDA", "AMD", "SMCI", "ARM"],
    # Cybersecurity basket
    ["CRWD", "PANW"],
    # Mega-cap big-tech — high QQQ weight, inter-correlated
    ["AAPL", "MSFT", "META", "AMZN", "GOOGL"],
    # Speculative growth — macro / risk-on cohort
    ["IONQ", "RKLB", "RXRX", "ACHR"],
]

MAX_CORRELATED_POSITIONS: int = 2

# ---------------------------------------------------------------------------
# Sector helpers
# ---------------------------------------------------------------------------


def get_sector(symbol: str) -> str:
    """Return the sector label for *symbol*, or 'unknown' if unmapped."""
    return SECTOR_MAP.get(symbol.upper(), "unknown")


def _open_symbols(open_positions: list[dict]) -> list[str]:
    """Extract symbol strings from an open-positions list."""
    return [p.get("symbol", "").upper() for p in open_positions]


# ---------------------------------------------------------------------------
# Concentration checks
# ---------------------------------------------------------------------------


def check_sector_concentration(
    symbol: str,
    open_positions: list[dict],
) -> tuple[bool, str]:
    """
    Return (True, '') when adding *symbol* keeps the sector count within
    MAX_SECTOR_POSITIONS, otherwise (False, reason).
    """
    symbol = symbol.upper()
    target_sector = get_sector(symbol)

    if target_sector == "unknown":
        # Unknown sector — no sector gate applied, warn only.
        return True, f"Symbol {symbol} has no sector mapping; sector gate skipped."

    sector_count = sum(
        1
        for sym in _open_symbols(open_positions)
        if get_sector(sym) == target_sector
    )

    if sector_count >= MAX_SECTOR_POSITIONS:
        return (
            False,
            f"Sector concentration limit hit: already {sector_count} open position(s) "
            f"in '{target_sector}' (max {MAX_SECTOR_POSITIONS}).",
        )

    return True, ""


def check_correlated_exposure(
    symbol: str,
    open_positions: list[dict],
) -> tuple[bool, str]:
    """
    Return (True, '') when adding *symbol* keeps correlated-group exposure
    within MAX_CORRELATED_POSITIONS, otherwise (False, reason).
    """
    symbol = symbol.upper()
    existing = set(_open_symbols(open_positions))

    for group in CORRELATED_GROUPS:
        group_upper = [s.upper() for s in group]
        if symbol not in group_upper:
            continue

        overlap = [s for s in group_upper if s in existing]
        if len(overlap) >= MAX_CORRELATED_POSITIONS:
            return (
                False,
                f"Correlated-group limit hit: {symbol} belongs to group "
                f"{group_upper}; already holding {overlap} "
                f"(max {MAX_CORRELATED_POSITIONS}).",
            )

    return True, ""


# ---------------------------------------------------------------------------
# Portfolio heat
# ---------------------------------------------------------------------------


def check_portfolio_heat(
    open_positions: list[dict],
    portfolio_value: float,
    new_risk_dollars: float,
) -> tuple[bool, str]:
    """
    Aggregate all at-risk dollars (open_positions[i]['risk_dollars']) plus
    *new_risk_dollars* and check against MAX_PORTFOLIO_HEAT.

    Each position dict is expected to have a 'risk_dollars' key.
    """
    if portfolio_value <= 0:
        return False, "Portfolio value must be positive."

    existing_risk = sum(
        float(p.get("risk_dollars", 0.0)) for p in open_positions
    )
    total_risk = existing_risk + new_risk_dollars
    heat = total_risk / portfolio_value

    if heat > MAX_PORTFOLIO_HEAT:
        return (
            False,
            f"Portfolio heat {heat:.1%} would exceed limit of "
            f"{MAX_PORTFOLIO_HEAT:.1%} "
            f"(existing ${existing_risk:,.2f} + new ${new_risk_dollars:,.2f} "
            f"on ${portfolio_value:,.2f} portfolio).",
        )

    return True, ""


# ---------------------------------------------------------------------------
# Position sizing — half-Kelly
# ---------------------------------------------------------------------------


def kelly_fraction(
    win_rate: float,
    avg_win_r: float = 2.0,
    avg_loss_r: float = 1.0,
) -> float:
    """
    Compute the half-Kelly fraction of portfolio equity to risk on a single
    trade, clamped to [0.005, 0.04] (0.5 % – 4 %).

    Kelly formula:  f* = (p * b - q) / b
      where b = avg_win_r / avg_loss_r, p = win_rate, q = 1 - p
    Half-Kelly:     f = f* / 2
    """
    if not (0.0 < win_rate < 1.0):
        return 0.005  # degenerate input — use minimum

    b = avg_win_r / max(avg_loss_r, 1e-9)
    q = 1.0 - win_rate
    full_kelly = (win_rate * b - q) / b
    half_kelly  = full_kelly / 2.0

    return max(0.005, min(0.04, half_kelly))


# ---------------------------------------------------------------------------
# Setup quality scorer
# ---------------------------------------------------------------------------


def score_setup_quality(ind_1h: dict, ind_1d: dict) -> float:
    """
    Score a trade setup from 0.0 (worst) to 1.0 (best) using multi-timeframe
    technical indicators.

    Expected keys (all optional — missing keys degrade score gracefully):
      ind_1h / ind_1d:
        'rsi'          — RSI value (14-period typical)
        'macd_hist'    — MACD histogram (positive = bullish momentum)
        'above_vwap'   — bool, price above intraday / daily VWAP
        'above_ema20'  — bool, price above 20-period EMA
        'above_ema50'  — bool, price above 50-period EMA
        'volume_ratio' — current vol / 20-day avg vol (>1.5 = confirmation)
    """
    score = 0.0
    max_score = 0.0

    def _add(points: float, condition: bool) -> None:
        nonlocal score, max_score
        max_score += points
        if condition:
            score += points

    # --- 1-hour signals (weight: 60 %) ---

    rsi_1h: Optional[float] = ind_1h.get("rsi")
    if rsi_1h is not None:
        # Bullish sweet spot: 45–65; not overbought, momentum positive
        _add(0.15, 45.0 <= rsi_1h <= 65.0)

    macd_hist_1h: Optional[float] = ind_1h.get("macd_hist")
    if macd_hist_1h is not None:
        _add(0.15, macd_hist_1h > 0)

    above_vwap_1h: Optional[bool] = ind_1h.get("above_vwap")
    if above_vwap_1h is not None:
        _add(0.10, bool(above_vwap_1h))

    above_ema20_1h: Optional[bool] = ind_1h.get("above_ema20")
    if above_ema20_1h is not None:
        _add(0.10, bool(above_ema20_1h))

    volume_ratio_1h: Optional[float] = ind_1h.get("volume_ratio")
    if volume_ratio_1h is not None:
        # Strong volume confirmation
        _add(0.10, volume_ratio_1h >= 1.5)

    # --- Daily signals (weight: 40 %) ---

    rsi_1d: Optional[float] = ind_1d.get("rsi")
    if rsi_1d is not None:
        # Daily: not overbought (< 70), trending positively (> 40)
        _add(0.10, 40.0 <= rsi_1d <= 70.0)

    macd_hist_1d: Optional[float] = ind_1d.get("macd_hist")
    if macd_hist_1d is not None:
        _add(0.10, macd_hist_1d > 0)

    above_ema50_1d: Optional[bool] = ind_1d.get("above_ema50")
    if above_ema50_1d is not None:
        # Daily EMA50 is a key trend filter
        _add(0.10, bool(above_ema50_1d))

    volume_ratio_1d: Optional[float] = ind_1d.get("volume_ratio")
    if volume_ratio_1d is not None:
        _add(0.10, volume_ratio_1d >= 1.2)

    if max_score == 0.0:
        return 0.0

    return round(score / max_score, 4)


# ---------------------------------------------------------------------------
# Stop and target calculators
# ---------------------------------------------------------------------------


def calculate_atr_stop(
    entry: float,
    atr: float,
    side: str,
    multiplier: float = 1.5,
) -> float:
    """
    Place stop *multiplier* × ATR away from entry in the direction of loss.

    side='long'  → stop below entry
    side='short' → stop above entry
    """
    side = side.lower()
    if side == "long":
        return round(entry - multiplier * atr, 4)
    elif side == "short":
        return round(entry + multiplier * atr, 4)
    else:
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")


def calculate_take_profit(
    entry: float,
    stop: float,
    side: str,
    rr: float = 2.5,
) -> float:
    """
    Place take-profit at *rr* × (entry - stop) distance from entry.

    side='long'  → target above entry
    side='short' → target below entry
    """
    side = side.lower()
    risk = abs(entry - stop)
    if side == "long":
        return round(entry + rr * risk, 4)
    elif side == "short":
        return round(entry - rr * risk, 4)
    else:
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")


# ---------------------------------------------------------------------------
# Master validation gate
# ---------------------------------------------------------------------------


def validate_trade(
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: float,
    portfolio_value: float,
    open_positions: list[dict],
    win_rate: float,
) -> tuple[bool, list[str]]:
    """
    Run ALL risk checks and return (approved: bool, rejection_reasons: list[str]).

    Checks performed (in order):
      1. Basic price sanity
      2. Risk/reward ratio ≥ 2.0
      3. Sector concentration
      4. Correlated-group exposure
      5. Portfolio heat (uses kelly_fraction to size the new trade)

    If *approved* is False, *rejection_reasons* lists every failing check so
    the caller can log or surface them to the user.
    """
    reasons: list[str] = []
    symbol = symbol.upper()
    side   = side.lower()

    # --- 1. Price sanity ---
    if entry <= 0 or stop <= 0 or target <= 0:
        reasons.append("Entry, stop, and target prices must all be positive.")

    if side == "long" and not (stop < entry < target):
        reasons.append(
            f"Long trade price ordering invalid: stop ({stop}) < entry ({entry}) "
            f"< target ({target}) required."
        )
    elif side == "short" and not (target < entry < stop):
        reasons.append(
            f"Short trade price ordering invalid: target ({target}) < entry ({entry}) "
            f"< stop ({stop}) required."
        )

    # --- 2. Risk/reward ---
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    if risk > 0:
        actual_rr = reward / risk
        if actual_rr < 2.0:
            reasons.append(
                f"Risk/reward {actual_rr:.2f}:1 is below minimum 2.0:1 "
                f"(risk ${risk:.4f}, reward ${reward:.4f})."
            )
    else:
        reasons.append("Entry and stop prices are identical; cannot compute R/R.")

    # --- 3. Sector concentration ---
    sector_ok, sector_msg = check_sector_concentration(symbol, open_positions)
    if not sector_ok:
        reasons.append(sector_msg)

    # --- 4. Correlated-group exposure ---
    corr_ok, corr_msg = check_correlated_exposure(symbol, open_positions)
    if not corr_ok:
        reasons.append(corr_msg)

    # --- 5. Portfolio heat ---
    # Size the new trade using half-Kelly to compute dollar risk.
    frac        = kelly_fraction(win_rate)
    risk_dollars = portfolio_value * frac  # max loss on this trade

    heat_ok, heat_msg = check_portfolio_heat(
        open_positions, portfolio_value, risk_dollars
    )
    if not heat_ok:
        reasons.append(heat_msg)

    approved = len(reasons) == 0
    return approved, reasons
