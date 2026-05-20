"""
signal_memory.py — Elite Memory Brain for Ascend Trader

The bot learns from every trade it has ever taken. Not just "did it win" but:
  - Which setup types work in which regimes
  - Which confidence bands are over/under-calibrated
  - Which symbols it is systematically wrong about
  - What the market environment looked like when it won vs lost
  - Whether its own confidence is drifting (overconfident streaks)
  - What it should avoid entirely right now

This module builds a complete self-knowledge profile and injects it directly
into Claude's prompt so every analysis starts from earned wisdom, not zero.

Architecture:
  SetupMemory    — per-symbol historical stats
  RegimeMemory   — per-regime win rate and best setups
  CalibrationMap — confidence bucket accuracy across all signals
  WeaknessReport — setups/symbols the bot is consistently wrong about
  LearningBrief  — one-paragraph "what I've learned this week" for Claude
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache config
# ---------------------------------------------------------------------------
_memory_cache: dict[str, "SetupMemory"] = {}
_regime_cache: dict[str, "RegimeMemory"] = {}
_calibration_cache: "CalibrationMap | None" = None
_weakness_cache: "WeaknessReport | None" = None
_learning_brief_cache: str = ""
_cache_built_at: datetime | None = None
_CACHE_TTL_HOURS = 6

_CONFIDENCE_BUCKETS = [
    ("0.60-0.69", 0.60, 0.70),
    ("0.70-0.79", 0.70, 0.80),
    ("0.80-0.89", 0.80, 0.90),
    ("0.90+",     0.90, 1.01),
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SetupMemory:
    symbol:      str
    sample_size: int
    win_rate_1h: float
    win_rate_1d: float
    win_rate_3d: float
    avg_return_1d: float
    avg_win_pct:   float
    avg_loss_pct:  float
    profit_factor: float          # (avg_win * wins) / (avg_loss * losses)
    best_setup_type:  str
    best_regime:      str
    worst_regime:     str
    memory_score:     float       # 0-1 trustworthiness
    confidence_calibration: dict[str, float] = field(default_factory=dict)
    streak_info:      str = ""    # e.g. "3 consecutive losses"
    regime:           str = "unknown"
    setup_type:       str = "unknown"
    best_conditions:  list[str] = field(default_factory=list)
    worst_conditions: list[str] = field(default_factory=list)


@dataclass
class RegimeMemory:
    regime:      str
    sample_size: int
    win_rate:    float
    avg_return:  float
    best_setups: list[str]        # setup types that work in this regime
    worst_setups: list[str]       # setup types that fail in this regime
    avg_holding_hours: float
    note: str = ""


@dataclass
class CalibrationMap:
    """
    Global confidence calibration across all signals.
    Tells us: when the bot says 80% confidence, is it actually right 80% of the time?
    """
    bucket_accuracy: dict[str, float]   # bucket → actual win rate
    bucket_sample:   dict[str, int]     # bucket → sample count
    overall_accuracy: float
    overconfident_buckets: list[str]    # where predicted > actual
    underconfident_buckets: list[str]   # where actual > predicted
    calibration_note: str


@dataclass
class WeaknessReport:
    """Systematic blindspots — what the bot consistently gets wrong."""
    worst_symbols:      list[tuple[str, float]]   # (symbol, win_rate)
    worst_setups:       list[tuple[str, float]]   # (setup_type, win_rate)
    worst_regimes:      list[tuple[str, float]]   # (regime, win_rate)
    loss_streak_symbols: list[str]                # symbols on 3+ consecutive losses
    avoid_now:          list[str]                 # symbols to skip this scan
    warning_text:       str


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------

def _bucket_label(confidence: float) -> str:
    for label, lo, hi in _CONFIDENCE_BUCKETS:
        if lo <= confidence < hi:
            return label
    return "0.90+"


def _compute_calibration(rows: list[dict]) -> dict[str, float]:
    bucket_wins: dict[str, list[float]] = {b[0]: [] for b in _CONFIDENCE_BUCKETS}
    for row in rows:
        conf = row.get("confidence") or 0.0
        ret  = row.get("return_1d_pct") or 0.0
        bucket_wins[_bucket_label(conf)].append(1.0 if ret > 0 else 0.0)
    return {
        label: round(sum(v) / len(v), 4)
        for label, v in bucket_wins.items()
        if v
    }


def _build_calibration_map(rows: list[dict]) -> CalibrationMap:
    bucket_wins:   dict[str, list[float]] = {b[0]: [] for b in _CONFIDENCE_BUCKETS}
    bucket_counts: dict[str, int]         = {b[0]: 0  for b in _CONFIDENCE_BUCKETS}

    for row in rows:
        conf = row.get("confidence") or 0.0
        ret  = row.get("return_1d_pct") or 0.0
        lbl  = _bucket_label(conf)
        bucket_wins[lbl].append(1.0 if ret > 0 else 0.0)
        bucket_counts[lbl] += 1

    accuracy: dict[str, float] = {}
    overconfident:   list[str] = []
    underconfident:  list[str] = []

    for label, lo, hi in _CONFIDENCE_BUCKETS:
        wins = bucket_wins[label]
        if not wins:
            continue
        actual = sum(wins) / len(wins)
        mid_conf = (lo + min(hi, 1.0)) / 2
        accuracy[label] = round(actual, 4)
        if actual < mid_conf - 0.08:
            overconfident.append(label)
        elif actual > mid_conf + 0.08:
            underconfident.append(label)

    all_returns = [r.get("return_1d_pct") or 0.0 for r in rows]
    overall = sum(1 for r in all_returns if r > 0) / max(len(all_returns), 1)

    parts = []
    if overconfident:
        parts.append(f"Overconfident at {', '.join(overconfident)} (claiming more certainty than results justify)")
    if underconfident:
        parts.append(f"Underconfident at {', '.join(underconfident)} (better than expected)")
    if not parts:
        parts.append("Confidence is well-calibrated across all buckets")

    return CalibrationMap(
        bucket_accuracy        = accuracy,
        bucket_sample          = {k: v for k, v in bucket_counts.items() if v > 0},
        overall_accuracy       = round(overall, 4),
        overconfident_buckets  = overconfident,
        underconfident_buckets = underconfident,
        calibration_note       = ". ".join(parts) + ".",
    )


# ---------------------------------------------------------------------------
# Regime memory builder
# ---------------------------------------------------------------------------

def _build_regime_memories(rows: list[dict]) -> dict[str, RegimeMemory]:
    by_regime: dict[str, list[dict]] = {}
    for row in rows:
        regime = _extract_regime(row)
        by_regime.setdefault(regime, []).append(row)

    result: dict[str, RegimeMemory] = {}
    for regime, rrows in by_regime.items():
        if len(rrows) < 5:
            continue
        wr   = _win_rate(rrows, "return_1d_pct")
        avgr = _avg(rrows, "return_1d_pct")

        # Best and worst setup types within this regime
        by_setup: dict[str, list[dict]] = {}
        for r in rrows:
            st = r.get("strategy") or "unknown"
            by_setup.setdefault(st, []).append(r)

        setup_wrs = {
            st: _win_rate(sr, "return_1d_pct")
            for st, sr in by_setup.items()
            if len(sr) >= 3
        }
        sorted_setups = sorted(setup_wrs, key=lambda k: setup_wrs[k], reverse=True)
        best_setups  = [s for s in sorted_setups[:3] if setup_wrs[s] >= 0.55]
        worst_setups = [s for s in sorted_setups[-3:] if setup_wrs[s] <= 0.45]

        # Average holding time
        holding_times = []
        for r in rrows:
            entry = r.get("entry_at") or r.get("checked_at")
            exit_ = r.get("checked_at")
            if entry and exit_:
                try:
                    dt_entry = datetime.fromisoformat(entry.replace("Z", "+00:00"))
                    dt_exit  = datetime.fromisoformat(exit_.replace("Z", "+00:00"))
                    holding_times.append((dt_exit - dt_entry).total_seconds() / 3600)
                except Exception:
                    pass
        avg_holding = sum(holding_times) / len(holding_times) if holding_times else 0

        note = (
            f"{regime.upper()}: {int(wr*100)}% win rate over {len(rrows)} signals. "
            f"Avg return: {avgr:+.2f}%. "
        )
        if best_setups:
            note += f"Best setups: {', '.join(best_setups)}. "
        if worst_setups:
            note += f"Avoid: {', '.join(worst_setups)}."

        result[regime] = RegimeMemory(
            regime             = regime,
            sample_size        = len(rrows),
            win_rate           = wr,
            avg_return         = avgr,
            best_setups        = best_setups,
            worst_setups       = worst_setups,
            avg_holding_hours  = round(avg_holding, 1),
            note               = note,
        )
    return result


# ---------------------------------------------------------------------------
# Weakness report builder
# ---------------------------------------------------------------------------

def _build_weakness_report(
    memory: dict[str, SetupMemory],
    regime_mem: dict[str, RegimeMemory],
    rows: list[dict],
) -> WeaknessReport:

    # Worst symbols (min 10 samples, win rate < 45%)
    worst_symbols = sorted(
        [(sym, m.win_rate_1d) for sym, m in memory.items()
         if m.sample_size >= 10 and m.win_rate_1d < 0.45],
        key=lambda x: x[1],
    )[:5]

    # Worst setups globally
    by_setup: dict[str, list[dict]] = {}
    for row in rows:
        st = row.get("strategy") or "unknown"
        by_setup.setdefault(st, []).append(row)
    setup_wrs = {
        st: _win_rate(sr, "return_1d_pct")
        for st, sr in by_setup.items()
        if len(sr) >= 10
    }
    worst_setups = sorted(
        [(st, wr) for st, wr in setup_wrs.items() if wr < 0.45],
        key=lambda x: x[1],
    )[:3]

    # Worst regimes
    worst_regimes = sorted(
        [(r, m.win_rate) for r, m in regime_mem.items()
         if m.sample_size >= 10 and m.win_rate < 0.45],
        key=lambda x: x[1],
    )[:3]

    # Loss streak detection (last 5 signals per symbol)
    loss_streak_syms: list[str] = []
    by_sym: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r.get("checked_at") or ""):
        sym = row.get("symbol", "")
        by_sym.setdefault(sym, []).append(row)
    for sym, sym_rows in by_sym.items():
        last5 = sym_rows[-5:]
        losses = [r for r in last5 if (r.get("return_1d_pct") or 0) < 0]
        if len(losses) >= 3:
            loss_streak_syms.append(sym)

    # Avoid list: symbols that are both systematically weak AND on a streak
    avoid = list({s for s, _ in worst_symbols} & set(loss_streak_syms))

    # Build warning text
    lines: list[str] = []
    if worst_symbols:
        sym_str = ", ".join(f"{s} ({int(wr*100)}%)" for s, wr in worst_symbols)
        lines.append(f"Weak symbols (sub-45% win rate): {sym_str}.")
    if worst_setups:
        st_str = ", ".join(f"{s} ({int(wr*100)}%)" for s, wr in worst_setups)
        lines.append(f"Underperforming setups: {st_str}.")
    if worst_regimes:
        r_str = ", ".join(f"{r} ({int(wr*100)}%)" for r, wr in worst_regimes)
        lines.append(f"Difficult regimes: {r_str}.")
    if loss_streak_syms:
        lines.append(f"Recent loss streaks (3+ of last 5): {', '.join(loss_streak_syms)}.")
    if avoid:
        lines.append(f"AVOID ENTIRELY: {', '.join(avoid)} — bad history AND recent losses.")
    if not lines:
        lines.append("No systematic weaknesses detected. Performance is healthy across all dimensions.")

    return WeaknessReport(
        worst_symbols        = worst_symbols,
        worst_setups         = worst_setups,
        worst_regimes        = worst_regimes,
        loss_streak_symbols  = loss_streak_syms,
        avoid_now            = avoid,
        warning_text         = " ".join(lines),
    )


# ---------------------------------------------------------------------------
# Learning brief — the "what I've learned" paragraph for Claude
# ---------------------------------------------------------------------------

def _build_learning_brief(
    memory: dict[str, SetupMemory],
    regime_mem: dict[str, RegimeMemory],
    calibration: CalibrationMap,
    weakness: WeaknessReport,
    rows: list[dict],
) -> str:
    """
    Build a concise, high-density learning brief that goes at the top of
    every Claude prompt. This is the most important output of the memory system —
    it transforms Claude from a generic analyst into one that actually knows
    its own track record.
    """
    n_signals   = len(rows)
    n_symbols   = len(memory)
    overall_wr  = calibration.overall_accuracy
    recent_rows = [r for r in rows if _is_recent(r, days=7)]
    recent_wr   = (
        sum(1 for r in recent_rows if (r.get("return_1d_pct") or 0) > 0) / max(len(recent_rows), 1)
        if recent_rows else None
    )

    lines: list[str] = [
        f"[ASCEND MEMORY — {n_signals} signals across {n_symbols} symbols]"
    ]

    # Overall performance
    if n_signals >= 20:
        lines.append(
            f"Overall 1-day win rate: {int(overall_wr*100)}%. "
            + (f"This week: {int(recent_wr*100)}% ({len(recent_rows)} signals)." if recent_wr is not None else "")
        )
    else:
        lines.append(
            f"Early learning phase ({n_signals} signals). "
            "Memory has low statistical weight — trust technicals over historical patterns."
        )

    # Calibration insight
    if calibration.overconfident_buckets:
        lines.append(
            f"CALIBRATION WARNING: When confidence is in the "
            f"{'/'.join(calibration.overconfident_buckets)} range, "
            f"actual results are significantly worse than predicted. "
            "Treat these signals as lower conviction than they appear."
        )
    if calibration.underconfident_buckets:
        lines.append(
            f"HIDDEN EDGE: Signals in the {'/'.join(calibration.underconfident_buckets)} "
            "confidence range outperform their stated confidence. These deserve more size."
        )

    # Best regime to trade in right now
    best_regime_entries = sorted(
        [(r, m) for r, m in regime_mem.items() if m.sample_size >= 10],
        key=lambda x: x[1].win_rate, reverse=True,
    )
    if best_regime_entries:
        best_r, best_m = best_regime_entries[0]
        lines.append(
            f"Best historical performance is in {best_r.upper()} regimes "
            f"({int(best_m.win_rate*100)}% win rate). "
            + (f"Preferred setups there: {', '.join(best_m.best_setups)}." if best_m.best_setups else "")
        )

    # Active weaknesses
    if weakness.avoid_now:
        lines.append(
            f"DO NOT TRADE: {', '.join(weakness.avoid_now)} — "
            "systematic losses AND recent losing streak. Skip these entirely."
        )
    elif weakness.worst_symbols:
        sym_str = ", ".join(s for s, _ in weakness.worst_symbols[:3])
        lines.append(
            f"Historically weak symbols: {sym_str}. Require higher confidence threshold."
        )

    # Setup-specific lessons
    best_setups_global = sorted(
        [(sym, m) for sym, m in memory.items() if m.sample_size >= 15],
        key=lambda x: x[1].win_rate_1d, reverse=True,
    )
    if best_setups_global:
        top = best_setups_global[0]
        lines.append(
            f"Strongest historical symbol: {top[0]} "
            f"({int(top[1].win_rate_1d*100)}% 1d win rate, {top[1].sample_size} signals). "
            f"Best conditions: {' | '.join(top[1].best_conditions[:2]) if top[1].best_conditions else 'n/a'}."
        )

    return " ".join(lines)


# ---------------------------------------------------------------------------
# Core memory builder
# ---------------------------------------------------------------------------

async def build_memory_from_outcomes(supabase_client: Any) -> dict[str, SetupMemory]:
    global _memory_cache, _regime_cache, _calibration_cache
    global _weakness_cache, _learning_brief_cache, _cache_built_at

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
                "earnings_catalyst, indicators, market_regime, entry_at)"
            )
            .order("checked_at", desc=True)
            .limit(5000)
            .execute()
        )
        rows: list[dict] = response.data or []
    except Exception as exc:
        logger.error("signal_memory: fetch failed — %s", exc)
        return _memory_cache

    # Flatten joined signals fields
    flat_rows: list[dict] = []
    for r in rows:
        sig = r.pop("signals", {}) or {}
        # Extract regime from indicators if available
        indicators = sig.get("indicators") or {}
        if isinstance(indicators, dict):
            regime = indicators.get("regime") or indicators.get("advanced_regime") or "unknown"
        else:
            regime = "unknown"
        r.update(sig)
        r["_regime"] = regime
        flat_rows.append(r)

    # ---- Per-symbol memory ----
    by_symbol: dict[str, list[dict]] = {}
    for row in flat_rows:
        sym = row.get("symbol", "UNKNOWN")
        by_symbol.setdefault(sym, []).append(row)

    memory: dict[str, SetupMemory] = {}
    for symbol, sym_rows in by_symbol.items():
        n = len(sym_rows)
        wr_1h  = _win_rate(sym_rows, "return_1h_pct")
        wr_1d  = _win_rate(sym_rows, "return_1d_pct")
        wr_3d  = _win_rate(sym_rows, "return_3d_pct")
        avg1d  = _avg(sym_rows, "return_1d_pct")

        wins   = [r.get("return_1d_pct") or 0.0 for r in sym_rows if (r.get("return_1d_pct") or 0) > 0]
        losses = [abs(r.get("return_1d_pct") or 0.0) for r in sym_rows if (r.get("return_1d_pct") or 0) < 0]
        avg_win  = sum(wins)  / len(wins)  if wins  else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        profit_factor = (avg_win * len(wins)) / max(avg_loss * len(losses), 0.001)

        calibration = _compute_calibration(sym_rows)
        m_score     = _memory_score(n, wr_1d, calibration)
        best_conds, worst_conds = _derive_conditions(sym_rows)

        # Streak detection
        sorted_rows = sorted(sym_rows, key=lambda r: r.get("checked_at") or "")
        streak_info = _detect_streak(sorted_rows)

        # Best/worst regime for this symbol
        regime_wrs: dict[str, float] = {}
        by_regime: dict[str, list[dict]] = {}
        for r in sym_rows:
            reg = r.get("_regime", "unknown")
            by_regime.setdefault(reg, []).append(r)
        for reg, rrows in by_regime.items():
            if len(rrows) >= 3:
                regime_wrs[reg] = _win_rate(rrows, "return_1d_pct")
        best_regime  = max(regime_wrs, key=regime_wrs.get) if regime_wrs else "unknown"
        worst_regime = min(regime_wrs, key=regime_wrs.get) if regime_wrs else "unknown"

        strategies = [r.get("strategy") or "unknown" for r in sym_rows]
        best_setup = _mode(strategies) or "unknown"

        memory[symbol] = SetupMemory(
            symbol          = symbol,
            sample_size     = n,
            win_rate_1h     = wr_1h,
            win_rate_1d     = wr_1d,
            win_rate_3d     = wr_3d,
            avg_return_1d   = avg1d,
            avg_win_pct     = round(avg_win,  4),
            avg_loss_pct    = round(avg_loss, 4),
            profit_factor   = round(profit_factor, 3),
            best_setup_type = best_setup,
            best_regime     = best_regime,
            worst_regime    = worst_regime,
            memory_score    = m_score,
            confidence_calibration = calibration,
            streak_info     = streak_info,
            regime          = "unknown",
            setup_type      = best_setup,
            best_conditions = best_conds,
            worst_conditions= worst_conds,
        )

    # ---- Global structures ----
    regime_mem  = _build_regime_memories(flat_rows)
    calibration = _build_calibration_map(flat_rows)
    weakness    = _build_weakness_report(memory, regime_mem, flat_rows)
    brief       = _build_learning_brief(memory, regime_mem, calibration, weakness, flat_rows)

    _memory_cache        = memory
    _regime_cache        = regime_mem
    _calibration_cache   = calibration
    _weakness_cache      = weakness
    _learning_brief_cache = brief
    _cache_built_at      = datetime.now(timezone.utc)

    logger.info(
        "signal_memory: built memory for %d symbols | %d signals | "
        "overall WR=%.0f%% | overconfident=%s | avoid=%s",
        len(memory), len(flat_rows),
        calibration.overall_accuracy * 100,
        calibration.overconfident_buckets,
        weakness.avoid_now,
    )
    return memory


# ---------------------------------------------------------------------------
# Historical accuracy lookup (called per-symbol during scan)
# ---------------------------------------------------------------------------

async def get_setup_historical_accuracy(
    supabase_client: Any,
    symbol: str,
    regime: str,
    setup_type: str,
    confidence: float,
) -> dict:
    if _should_refresh_cache():
        await build_memory_from_outcomes(supabase_client)

    mem = _memory_cache.get(symbol)
    weakness = _weakness_cache

    # Hard avoid: symbol is on our weakness list
    if weakness and symbol in weakness.avoid_now:
        penalised = round(max(0.0, confidence - 0.15), 4)
        return {
            "sample_size":          mem.sample_size if mem else 0,
            "historical_win_rate":  mem.win_rate_1d if mem else None,
            "calibrated_confidence": penalised,
            "memory_note": (
                f"⚠️ {symbol} is on the AVOID list — systematic losses AND recent streak. "
                f"Confidence reduced by 0.15 to {penalised:.0%}."
            ),
            "should_boost": False,
            "boost_amount": penalised - confidence,
        }

    if mem is None or mem.sample_size < 20:
        sample = mem.sample_size if mem else 0
        return {
            "sample_size":          sample,
            "historical_win_rate":  None,
            "calibrated_confidence": confidence,
            "memory_note": (
                f"Insufficient data for {symbol} "
                f"({sample} signals, need ≥20) — using raw confidence."
            ),
            "should_boost": False,
            "boost_amount": 0.0,
        }

    hist_wr    = mem.win_rate_1d
    cal_conf   = calibrate_confidence(confidence, hist_wr, mem.sample_size)

    # Additional penalty if this is the symbol's worst regime
    if mem.worst_regime == regime and mem.sample_size >= 20:
        cal_conf = round(max(0.0, cal_conf - 0.05), 4)

    # Streak penalty
    if "consecutive losses" in mem.streak_info:
        try:
            streak_n = int(mem.streak_info.split()[0])
            if streak_n >= 3:
                cal_conf = round(max(0.0, cal_conf - 0.04 * min(streak_n - 2, 3)), 4)
        except (ValueError, IndexError):
            pass

    cal_conf   = min(cal_conf, 0.95)
    boost_amt  = round(cal_conf - confidence, 4)

    # Calibration-level note
    bucket     = _bucket_label(confidence)
    bucket_wr  = mem.confidence_calibration.get(bucket)
    cal_note   = ""
    if bucket_wr is not None:
        diff = bucket_wr - (float(bucket.split("-")[0].replace("+", "")) if "+" not in bucket else 0.90)
        status = "well-calibrated" if abs(diff) < 0.08 else ("under-confident" if diff > 0 else "over-confident")
        cal_note = (
            f"Your {int(confidence*100)}%-confidence {symbol} signals have historically "
            f"been correct {int(bucket_wr*100)}% of the time ({status})."
        )

    pf_note = (
        f" Profit factor: {mem.profit_factor:.2f}x "
        f"(avg win {mem.avg_win_pct:.2f}% / avg loss {mem.avg_loss_pct:.2f}%)."
    )
    streak_note = f" {mem.streak_info}." if mem.streak_info else ""

    memory_note = (
        f"[{symbol} memory: {mem.sample_size} signals | "
        f"WR 1h={int(mem.win_rate_1h*100)}% 1d={int(mem.win_rate_1d*100)}% 3d={int(mem.win_rate_3d*100)}% | "
        f"avg {mem.avg_return_1d:+.2f}%]{pf_note}"
        f"{' ' + cal_note if cal_note else ''}"
        f"{streak_note}"
        + (f" Best regime: {mem.best_regime} | Worst: {mem.worst_regime}." if mem.best_regime != "unknown" else "")
    )

    if boost_amt > 0:
        memory_note += f" Memory BOOSTS confidence by {boost_amt:+.2f}."
    elif boost_amt < 0:
        memory_note += f" Memory REDUCES confidence by {boost_amt:.2f}."

    return {
        "sample_size":          mem.sample_size,
        "historical_win_rate":  hist_wr,
        "calibrated_confidence": cal_conf,
        "memory_note":          memory_note,
        "should_boost":         boost_amt > 0,
        "boost_amount":         boost_amt,
    }


# ---------------------------------------------------------------------------
# Calibration function
# ---------------------------------------------------------------------------

def calibrate_confidence(
    raw_confidence: float,
    historical_win_rate: float,
    sample_size: int,
) -> float:
    if sample_size < 20:
        return raw_confidence

    weight = 0.5 if sample_size < 50 else 1.0
    delta  = historical_win_rate - raw_confidence

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
# Prompt builders
# ---------------------------------------------------------------------------

def build_memory_prompt_section(memory: dict[str, SetupMemory], symbol: str) -> str:
    """Per-symbol memory block for Claude prompt."""
    mem = memory.get(symbol)

    if mem is None or mem.sample_size == 0:
        return (
            "SIGNAL MEMORY: No historical outcomes on record for this symbol. "
            "Rely on technicals and regime context only."
        )

    lines: list[str] = [
        f"SIGNAL MEMORY [{symbol}]: {mem.sample_size} past signals | "
        f"Win rate: 1h={int(mem.win_rate_1h*100)}% 1d={int(mem.win_rate_1d*100)}% 3d={int(mem.win_rate_3d*100)}% | "
        f"Avg 1d return: {mem.avg_return_1d:+.2f}% | "
        f"Profit factor: {mem.profit_factor:.2f}x"
    ]

    if mem.confidence_calibration:
        cal_parts = []
        for bucket, actual_wr in sorted(mem.confidence_calibration.items()):
            try:
                floor = float(bucket.split("-")[0].replace("+", ""))
            except (ValueError, IndexError):
                floor = 0.90
            diff = actual_wr - floor
            tag = "✓ calibrated" if abs(diff) < 0.08 else ("↑ under-confident" if diff > 0.08 else "↓ OVER-CONFIDENT")
            cal_parts.append(f"{bucket}→{int(actual_wr*100)}% ({tag})")
        lines.append("Confidence calibration: " + " | ".join(cal_parts))

    if mem.best_regime != "unknown":
        lines.append(
            f"Best in {mem.best_regime.upper()} regime | Worst in {mem.worst_regime.upper()} regime"
        )

    if mem.best_conditions:
        lines.append("Best conditions: " + " | ".join(mem.best_conditions))
    if mem.worst_conditions:
        lines.append("Watch out: " + " | ".join(mem.worst_conditions))
    if mem.streak_info:
        lines.append(f"Recent streak: {mem.streak_info}")

    quality = (
        "HIGH" if mem.memory_score >= 0.7
        else "MODERATE" if mem.memory_score >= 0.4
        else "LOW"
    )
    lines.append(f"Memory quality: {quality} (score={mem.memory_score:.2f})")

    return " | ".join(lines)


def get_learning_brief() -> str:
    """Return the global learning brief for injection at the top of Claude prompts."""
    return _learning_brief_cache or (
        "ASCEND MEMORY: Insufficient historical data. Bot is in learning mode — "
        "rely on technical analysis and fundamental catalysts."
    )


def get_regime_memory(regime: str) -> RegimeMemory | None:
    return _regime_cache.get(regime)


def get_calibration_map() -> CalibrationMap | None:
    return _calibration_cache


def get_weakness_report() -> WeaknessReport | None:
    return _weakness_cache


def get_symbols_to_avoid() -> list[str]:
    """Return symbols the bot should skip entirely based on weakness report."""
    if _weakness_cache:
        return _weakness_cache.avoid_now
    return []


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _win_rate(rows: list[dict], field_name: str) -> float:
    valid = [r.get(field_name) for r in rows if r.get(field_name) is not None]
    if not valid:
        return 0.0
    return round(sum(1 for v in valid if v > 0) / len(valid), 4)


def _avg(rows: list[dict], field_name: str) -> float:
    valid = [r.get(field_name) for r in rows if r.get(field_name) is not None]
    if not valid:
        return 0.0
    return round(sum(valid) / len(valid), 4)


def _mode(items: list) -> Any:
    if not items:
        return None
    return max(set(items), key=items.count)


def _extract_regime(row: dict) -> str:
    return row.get("_regime") or row.get("regime") or "unknown"


def _is_recent(row: dict, days: int = 7) -> bool:
    ts = row.get("checked_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt > datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        return False


def _detect_streak(sorted_rows: list[dict]) -> str:
    """Detect current win/loss streak from most recent signals."""
    if not sorted_rows:
        return ""
    results = [
        "win" if (r.get("return_1d_pct") or 0) > 0 else "loss"
        for r in sorted_rows
    ]
    if not results:
        return ""
    last = results[-1]
    count = 0
    for r in reversed(results):
        if r == last:
            count += 1
        else:
            break
    if count >= 2:
        return f"{count} consecutive {last}s"
    return ""


def _memory_score(sample_size: int, win_rate_1d: float, calibration: dict[str, float]) -> float:
    depth_score       = min(sample_size / 100.0, 1.0)
    edge_score        = min(abs(win_rate_1d - 0.50) * 2.0, 1.0)
    calibration_score = len(calibration) / len(_CONFIDENCE_BUCKETS)
    return round((depth_score * 0.5) + (edge_score * 0.3) + (calibration_score * 0.2), 4)


def _derive_conditions(rows: list[dict]) -> tuple[list[str], list[str]]:
    best: list[str] = []
    worst: list[str] = []

    stopped_out  = [r for r in rows if r.get("hit_stop")]
    took_profit  = [r for r in rows if r.get("hit_take_profit")]
    stop_rate    = len(stopped_out) / max(len(rows), 1)
    tp_rate      = len(took_profit) / max(len(rows), 1)

    if tp_rate > 0.55:
        best.append(f"Take-profit hit {int(tp_rate*100)}% of the time — strong follow-through")
    if stop_rate > 0.40:
        worst.append(f"Stop-out rate {int(stop_rate*100)}% — entries often poorly timed")

    returns = sorted([r.get("return_1d_pct") or 0.0 for r in rows], reverse=True)
    if returns:
        top_q = returns[:max(1, len(returns) // 4)]
        bot_q = returns[-(max(1, len(returns) // 4)):]
        if top_q and top_q[0] > 2.0:
            best.append(f"Top quartile averages {sum(top_q)/len(top_q):.1f}% at 1d")
        if bot_q and bot_q[-1] < -1.5:
            worst.append(f"Bottom quartile averages {sum(bot_q)/len(bot_q):.1f}% at 1d")

    adverse = [r.get("max_adverse_pct") or 0.0 for r in rows]
    avg_adv  = sum(adverse) / max(len(adverse), 1)
    if avg_adv > 1.5:
        worst.append(f"High avg adverse excursion {avg_adv:.1f}% — wide initial drawdown")
    elif avg_adv < 0.5:
        best.append("Low adverse excursion — setups move immediately in direction")

    return best[:4], worst[:4]


def _should_refresh_cache() -> bool:
    if _cache_built_at is None:
        return True
    return (datetime.now(timezone.utc) - _cache_built_at) > timedelta(hours=_CACHE_TTL_HOURS)
