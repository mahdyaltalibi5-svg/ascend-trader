"""
signal_memory.py — Historical signal memory and accuracy tracking for Ascend Trader.

The bot learns from its own past signals: what worked, what didn't, and under
which conditions. Builds per-symbol SetupMemory objects from signal_outcomes
joined with signals, calibrates confidence, and injects memory context into
Claude prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------
_memory_cache: dict[str, "SetupMemory"] = {}
_cache_built_at: datetime | None = None
_CACHE_TTL_HOURS = 6


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SetupMemory:
    """Aggregated historical memory for a specific symbol."""

    symbol: str
    regime: str                              # most common regime seen for this symbol
    setup_type: str                          # most common setup type classified
    sample_size: int                         # total outcomes on record

    win_rate_1h: float                       # fraction of signals that were positive at 1 h
    win_rate_1d: float                       # fraction of signals that were positive at 1 d
    win_rate_3d: float                       # fraction of signals that were positive at 3 d

    avg_return_1d: float                     # mean 1-day return across all signals (%)

    best_conditions: list[str] = field(default_factory=list)   # qualitative labels
    worst_conditions: list[str] = field(default_factory=list)  # qualitative labels

    # Mapping from confidence bucket label → actual historical win rate
    # buckets: "0.60-0.69", "0.70-0.79", "0.80-0.89", "0.90+"
    confidence_calibration: dict[str, float] = field(default_factory=dict)

    # 0-1 composite quality score for how trustworthy the memory is
    memory_score: float = 0.0


# ---------------------------------------------------------------------------
# Confidence bucketing helpers
# ---------------------------------------------------------------------------

_CONFIDENCE_BUCKETS = [
    ("0.60-0.69", 0.60, 0.70),
    ("0.70-0.79", 0.70, 0.80),
    ("0.80-0.89", 0.80, 0.90),
    ("0.90+",     0.90, 1.01),
]


def _bucket_label(confidence: float) -> str:
    """Return the bucket label for a raw confidence value."""
    for label, lo, hi in _CONFIDENCE_BUCKETS:
        if lo <= confidence < hi:
            return label
    return "0.90+"


def _compute_calibration(rows: list[dict]) -> dict[str, float]:
    """
    Group rows by confidence bucket and compute actual win rate per bucket.

    Each row must have 'confidence' (float) and 'return_1d_pct' (float).
    """
    bucket_wins: dict[str, list[float]] = {b[0]: [] for b in _CONFIDENCE_BUCKETS}

    for row in rows:
        conf = row.get("confidence") or 0.0
        ret = row.get("return_1d_pct") or 0.0
        label = _bucket_label(conf)
        bucket_wins[label].append(1.0 if ret > 0 else 0.0)

    calibration: dict[str, float] = {}
    for label, outcomes in bucket_wins.items():
        if outcomes:
            calibration[label] = round(sum(outcomes) / len(outcomes), 4)
    return calibration


def _memory_score(sample_size: int, win_rate_1d: float, calibration: dict[str, float]) -> float:
    """
    Composite trustworthiness score for a SetupMemory object.

    Components:
    - Sample depth:   more samples → higher trust (saturates at 100)
    - Win rate edge:  distance from 0.50 coin flip
    - Calibration:    how many buckets have enough data
    """
    depth_score = min(sample_size / 100.0, 1.0)
    edge_score = min(abs(win_rate_1d - 0.50) * 2.0, 1.0)
    calibration_score = len(calibration) / len(_CONFIDENCE_BUCKETS)
    return round((depth_score * 0.5) + (edge_score * 0.3) + (calibration_score * 0.2), 4)


def _derive_conditions(rows: list[dict]) -> tuple[list[str], list[str]]:
    """
    Derive qualitative best/worst conditions from outcome rows.

    Uses simple heuristics on outcome_score and ancillary fields.
    """
    best: list[str] = []
    worst: list[str] = []

    # Group by hit_stop vs hit_take_profit
    stopped_out = [r for r in rows if r.get("hit_stop")]
    took_profit = [r for r in rows if r.get("hit_take_profit")]

    stop_rate = len(stopped_out) / max(len(rows), 1)
    tp_rate = len(took_profit) / max(len(rows), 1)

    if tp_rate > 0.55:
        best.append("Take-profit frequently hit — momentum follows through")
    if stop_rate > 0.40:
        worst.append("High stop-out rate — entries often poorly timed")

    # Best/worst by 1d return quartiles
    returns = sorted(
        [r.get("return_1d_pct") or 0.0 for r in rows],
        reverse=True,
    )
    if returns:
        top_q = returns[: max(1, len(returns) // 4)]
        bot_q = returns[-(max(1, len(returns) // 4)):]
        if top_q and top_q[0] > 2.0:
            best.append(f"Top quartile returns avg {sum(top_q)/len(top_q):.1f}% at 1d")
        if bot_q and bot_q[-1] < -1.5:
            worst.append(f"Bottom quartile returns avg {sum(bot_q)/len(bot_q):.1f}% at 1d")

    # Adverse excursion
    adverse = [r.get("max_adverse_pct") or 0.0 for r in rows]
    avg_adverse = sum(adverse) / max(len(adverse), 1)
    if avg_adverse > 1.5:
        worst.append(f"High avg adverse excursion ({avg_adverse:.1f}%) — wide initial drawdown")
    elif avg_adverse < 0.5:
        best.append("Low adverse excursion — setups tend to move immediately in direction")

    return best[:4], worst[:4]


# ---------------------------------------------------------------------------
# Core async functions
# ---------------------------------------------------------------------------

async def build_memory_from_outcomes(supabase_client: Any) -> dict[str, SetupMemory]:
    """
    Query Supabase for all signal_outcomes joined with signals.

    Groups by symbol and builds a SetupMemory per symbol.
    Returns a dict keyed by symbol.  Also populates the module-level cache.
    """
    global _memory_cache, _cache_built_at

    try:
        response = (
            supabase_client
            .table("signal_outcomes")
            .select(
                "id, signal_id, symbol, signal, entry_price, "
                "return_1h_pct, return_1d_pct, return_3d_pct, "
                "max_favorable_pct, max_adverse_pct, "
                "hit_stop, hit_take_profit, outcome_score, checked_at, "
                "signals!inner(strategy, strength, confidence, criteria_met, "
                "earnings_catalyst, indicators)"
            )
            .order("checked_at", desc=True)
            .limit(5000)
            .execute()
        )
        rows: list[dict] = response.data or []
    except Exception as exc:
        logger.error("signal_memory: failed to fetch outcomes — %s", exc)
        return _memory_cache  # return stale cache rather than crash

    # Flatten joined signals fields into each row
    flat_rows: list[dict] = []
    for r in rows:
        sig = r.pop("signals", {}) or {}
        r.update(sig)
        flat_rows.append(r)

    # Group by symbol
    by_symbol: dict[str, list[dict]] = {}
    for row in flat_rows:
        sym = row.get("symbol", "UNKNOWN")
        by_symbol.setdefault(sym, []).append(row)

    memory: dict[str, SetupMemory] = {}

    for symbol, sym_rows in by_symbol.items():
        n = len(sym_rows)

        # Win rates per horizon
        wr_1h = _win_rate(sym_rows, "return_1h_pct")
        wr_1d = _win_rate(sym_rows, "return_1d_pct")
        wr_3d = _win_rate(sym_rows, "return_3d_pct")
        avg_ret_1d = _avg(sym_rows, "return_1d_pct")

        calibration = _compute_calibration(sym_rows)
        best_conds, worst_conds = _derive_conditions(sym_rows)
        m_score = _memory_score(n, wr_1d, calibration)

        # Most common strategy as proxy for setup_type / regime
        strategies = [r.get("strategy") or "unknown" for r in sym_rows]
        most_common_strategy = _mode(strategies) or "unknown"

        memory[symbol] = SetupMemory(
            symbol=symbol,
            regime="unknown",          # regime joined separately if available
            setup_type=most_common_strategy,
            sample_size=n,
            win_rate_1h=wr_1h,
            win_rate_1d=wr_1d,
            win_rate_3d=wr_3d,
            avg_return_1d=avg_ret_1d,
            best_conditions=best_conds,
            worst_conditions=worst_conds,
            confidence_calibration=calibration,
            memory_score=m_score,
        )

    _memory_cache = memory
    _cache_built_at = datetime.now(timezone.utc)
    logger.info("signal_memory: built memory for %d symbols from %d outcomes", len(memory), len(flat_rows))
    return memory


async def get_setup_historical_accuracy(
    supabase_client: Any,
    symbol: str,
    regime: str,
    setup_type: str,
    confidence: float,
) -> dict:
    """
    Return historical accuracy metadata for a specific setup.

    Refreshes the module-level cache if stale (> 6 hours old).
    Returns a dict with: sample_size, historical_win_rate, calibrated_confidence,
    memory_note (str), should_boost (bool), boost_amount (float).
    """
    # Refresh cache if needed
    if _should_refresh_cache():
        await build_memory_from_outcomes(supabase_client)

    mem = _memory_cache.get(symbol)

    if mem is None or mem.sample_size < 20:
        return {
            "sample_size": mem.sample_size if mem else 0,
            "historical_win_rate": None,
            "calibrated_confidence": confidence,
            "memory_note": (
                f"Insufficient historical data for {symbol} "
                f"({mem.sample_size if mem else 0} signals, need ≥20) — using raw confidence."
            ),
            "should_boost": False,
            "boost_amount": 0.0,
        }

    hist_wr = mem.win_rate_1d
    cal_conf = calibrate_confidence(confidence, hist_wr, mem.sample_size)
    boost_amount = round(cal_conf - confidence, 4)
    should_boost = boost_amount > 0

    bucket = _bucket_label(confidence)
    bucket_wr = mem.confidence_calibration.get(bucket)

    if bucket_wr is not None:
        calibration_note = (
            f"Your {int(confidence*100)}%-confidence signals for {symbol} "
            f"have historically been correct {int(bucket_wr*100)}% of the time "
            f"({'well-calibrated' if abs(bucket_wr - confidence) < 0.08 else 'poorly-calibrated'})."
        )
    else:
        calibration_note = f"No bucket data for {int(confidence*100)}% confidence signals on {symbol}."

    memory_note = (
        f"Based on {mem.sample_size} past signals, {symbol} has won "
        f"{int(hist_wr*100)}% of the time at 1d horizon. {calibration_note}"
    )
    if should_boost:
        memory_note += f" Confidence boosted by {boost_amount:+.2f} based on memory."
    elif boost_amount < 0:
        memory_note += f" Confidence reduced by {boost_amount:.2f} based on memory."

    return {
        "sample_size": mem.sample_size,
        "historical_win_rate": hist_wr,
        "calibrated_confidence": cal_conf,
        "memory_note": memory_note,
        "should_boost": should_boost,
        "boost_amount": boost_amount,
    }


# ---------------------------------------------------------------------------
# Pure calibration function
# ---------------------------------------------------------------------------

def calibrate_confidence(
    raw_confidence: float,
    historical_win_rate: float,
    sample_size: int,
) -> float:
    """
    Adjust raw model confidence based on empirical historical win rate.

    Rules (applied in order, only one fires):
    - sample_size < 20  → return raw_confidence unchanged (insufficient data)
    - sample_size 20-49 → halve the adjustment magnitude (low-weight zone)
    - hist_win_rate > raw_confidence + 0.15 → boost by up to +0.05
    - hist_win_rate < raw_confidence - 0.15 → reduce by up to -0.12 (asymmetric: penalties > boosts)
    - hist_win_rate < raw_confidence - 0.30 → reduce by up to -0.18 (severe underperformance)
    - Otherwise → no change

    Result is always capped at 0.95. Never boosted above 0.95.
    """
    if sample_size < 20:
        return raw_confidence

    weight = 0.5 if sample_size < 50 else 1.0
    delta = historical_win_rate - raw_confidence

    if delta > 0.15:
        adjustment = +0.05 * weight
    elif delta < -0.30:
        adjustment = -0.18 * weight
    elif delta < -0.15:
        adjustment = -0.12 * weight
    else:
        adjustment = 0.0

    return round(min(raw_confidence + adjustment, 0.95), 4)


# ---------------------------------------------------------------------------
# Prompt section builder
# ---------------------------------------------------------------------------

def build_memory_prompt_section(memory: dict[str, "SetupMemory"], symbol: str) -> str:
    """
    Format signal memory for injection into a Claude prompt.

    Returns a plain-text block starting with 'SIGNAL MEMORY:'.
    If no memory exists for the symbol returns a short note.
    """
    mem = memory.get(symbol)

    if mem is None or mem.sample_size == 0:
        return (
            "SIGNAL MEMORY: No historical outcomes on record for this symbol. "
            "Treat this as a new setup — rely on technicals and regime context only."
        )

    lines: list[str] = [
        f"SIGNAL MEMORY: Based on {mem.sample_size} past signals, this setup has won "
        f"{int(mem.win_rate_1d * 100)}% of the time at 1d horizon "
        f"(1h: {int(mem.win_rate_1h * 100)}%, 3d: {int(mem.win_rate_3d * 100)}%). "
        f"Avg 1d return: {mem.avg_return_1d:+.2f}%."
    ]

    # Calibration for most likely confidence band
    if mem.confidence_calibration:
        cal_parts = []
        for bucket, actual_wr in sorted(mem.confidence_calibration.items()):
            bucket_floor = bucket.split("-", 1)[0].replace("+", "")
            diff = actual_wr - float(bucket_floor)
            direction = "over-confident" if diff < -0.05 else ("under-confident" if diff > 0.05 else "well-calibrated")
            cal_parts.append(f"{bucket} signals → {int(actual_wr*100)}% actual ({direction})")
        lines.append("Confidence calibration: " + "; ".join(cal_parts) + ".")

    # Best conditions
    if mem.best_conditions:
        lines.append("Best conditions: " + " | ".join(mem.best_conditions) + ".")

    # Worst conditions
    if mem.worst_conditions:
        lines.append("Watch out for: " + " | ".join(mem.worst_conditions) + ".")

    # Memory quality
    if mem.memory_score >= 0.7:
        lines.append(f"Memory quality: HIGH (score {mem.memory_score:.2f}) — strong historical basis.")
    elif mem.memory_score >= 0.4:
        lines.append(f"Memory quality: MODERATE (score {mem.memory_score:.2f}) — use with some caution.")
    else:
        lines.append(f"Memory quality: LOW (score {mem.memory_score:.2f}) — limited data, weight lightly.")

    return " ".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _win_rate(rows: list[dict], field: str) -> float:
    valid = [r.get(field) for r in rows if r.get(field) is not None]
    if not valid:
        return 0.0
    return round(sum(1 for v in valid if v > 0) / len(valid), 4)


def _avg(rows: list[dict], field: str) -> float:
    valid = [r.get(field) for r in rows if r.get(field) is not None]
    if not valid:
        return 0.0
    return round(sum(valid) / len(valid), 4)


def _mode(items: list) -> Any:
    if not items:
        return None
    return max(set(items), key=items.count)


def _should_refresh_cache() -> bool:
    if _cache_built_at is None:
        return True
    age = datetime.now(timezone.utc) - _cache_built_at
    return age > timedelta(hours=_CACHE_TTL_HOURS)
