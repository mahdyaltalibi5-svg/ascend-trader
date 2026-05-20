"""
signal_attribution.py — Per-Component Signal Accuracy Tracking

After each trade outcome is evaluated, this module writes an attribution row
that records exactly which signal components were active and whether the trade
won.  Over time, aggregating these rows tells us:

  - Is the RS boost actually predictive?
  - Does high options flow conviction improve outcomes?
  - Is the insider flow signal adding alpha or noise?
  - Which catalyst types consistently lead to winners?

This feeds back into signal_memory.py's learning brief so Claude knows
empirically which signals to weight more heavily.

Attribution is written by evaluate_signal_outcomes() in main.py.
Read via get_component_stats() for the learning brief.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ComponentStats:
    name:        str
    total_trades: int       = 0
    wins:         int       = 0
    losses:       int       = 0
    avg_r:        float     = 0.0
    win_rate:     float     = 0.0
    edge:         float     = 0.0   # win_rate - 0.50 (positive = alpha)
    signal:       str       = "neutral"  # "alpha" | "noise" | "drag" | "neutral"

    def summary(self) -> str:
        if self.total_trades < 5:
            return f"{self.name}: insufficient data ({self.total_trades} trades)"
        edge_str = f"+{self.edge*100:.1f}%" if self.edge >= 0 else f"{self.edge*100:.1f}%"
        return (
            f"{self.name}: {self.win_rate*100:.0f}% WR over {self.total_trades} trades "
            f"| avg R={self.avg_r:.2f} | edge={edge_str} [{self.signal.upper()}]"
        )


@dataclass
class AttributionInsights:
    component_stats: list[ComponentStats] = field(default_factory=list)
    best_component:  str | None = None
    worst_component: str | None = None
    reliable_count:  int = 0   # components with >= 10 trades and WR > 55%
    noise_count:     int = 0   # components with >= 10 trades and WR < 45%
    prompt_section:  str = ""


# ---------------------------------------------------------------------------
# Attribution row builder
# ---------------------------------------------------------------------------

def build_attribution_row(
    signal_id: str,
    symbol: str,
    signal_record: dict,
    outcome_record: dict,
) -> dict:
    """
    Merge a signal record + its outcome into a flat attribution row for
    insertion into the signal_attribution table.

    signal_record  : row from the signals table (includes indicators JSONB)
    outcome_record : row from signal_outcomes table
    """
    indicators = signal_record.get("indicators") or {}
    setup      = indicators.get("setup") or {}
    catalyst   = indicators.get("catalyst") or {}
    memory_adj = setup.get("memory_adjustment") or {}

    # Pull boost values stored in the payload/indicators
    # These were written by scan_symbol() as signal["rs_boost"] etc.
    # They live at the top of the indicators JSONB in some builds or in
    # the scan_events payload.  We fall back to 0.0 gracefully.
    rs_boost      = float(signal_record.get("rs_boost", 0.0) or 0.0)
    insider_boost = float(signal_record.get("insider_boost", 0.0) or 0.0)
    opts_boost    = float(signal_record.get("options_flow_boost", 0.0) or 0.0)
    si_boost      = float(signal_record.get("short_interest_boost", 0.0) or 0.0)
    mem_boost     = float(memory_adj.get("calibration_delta", 0.0) or 0.0)
    cat_score     = float(catalyst.get("total_score", 0.0) or 0.0)

    conf_raw   = float(signal_record.get("confidence_raw", signal_record.get("strength", 0.0)) or 0.0)
    conf_final = float(signal_record.get("confidence", 0.0) or 0.0)

    outcome_score = float(outcome_record.get("outcome_score", 0.0) or 0.0)
    r_multiple    = float(outcome_record.get("return_1d_pct", 0.0) or 0.0)
    hit_stop      = bool(outcome_record.get("hit_stop", False))
    hit_target    = bool(outcome_record.get("hit_take_profit", False))
    won           = hit_target or (outcome_score > 0 and not hit_stop)

    # Attribution flags: did this component push in the winning direction?
    side = signal_record.get("signal", "hold")
    rs_contributed      = (rs_boost > 0.01 and won) or (rs_boost < -0.01 and not won)
    insider_contributed = (insider_boost > 0.01 and won) or (insider_boost < -0.01 and not won)
    opts_contributed    = (opts_boost > 0.01 and won) or (opts_boost < -0.01 and not won)
    si_contributed      = (si_boost > 0.01 and won) or (si_boost < -0.01 and not won)
    cat_contributed     = cat_score >= 0.60 and won

    return {
        "signal_id":             signal_id,
        "symbol":                symbol,
        "setup_type":            signal_record.get("setup_type"),
        "regime":                (signal_record.get("market_regime") or {}).get("advanced_regime"),
        "signal_side":           side,
        "confidence_raw":        round(conf_raw, 4),
        "confidence_final":      round(conf_final, 4),
        "catalyst_score":        round(cat_score, 4),
        "rs_boost":              round(rs_boost, 4),
        "insider_boost":         round(insider_boost, 4),
        "options_flow_boost":    round(opts_boost, 4),
        "short_interest_boost":  round(si_boost, 4),
        "memory_boost":          round(mem_boost, 4),
        "outcome_score":         round(outcome_score, 4),
        "r_multiple":            round(r_multiple, 4),
        "won":                   won,
        "hit_stop":              hit_stop,
        "hit_target":            hit_target,
        "rs_contributed":        rs_contributed,
        "insider_contributed":   insider_contributed,
        "options_contributed":   opts_contributed,
        "short_interest_contributed": si_contributed,
        "catalyst_contributed":  cat_contributed,
    }


# ---------------------------------------------------------------------------
# Aggregated stats reader
# ---------------------------------------------------------------------------

def _make_stats(name: str, rows: list[dict], active_key: str) -> ComponentStats:
    """Build ComponentStats for rows where the component was active (boost != 0)."""
    active = [r for r in rows if abs(float(r.get(active_key, 0.0) or 0.0)) > 0.005]
    if not active:
        return ComponentStats(name=name)

    wins       = sum(1 for r in active if r.get("won"))
    losses     = len(active) - wins
    win_rate   = wins / len(active) if active else 0.0
    avg_r      = sum(float(r.get("r_multiple", 0.0) or 0.0) for r in active) / len(active)
    edge       = win_rate - 0.50

    if len(active) >= 10:
        if win_rate >= 0.58:
            signal = "alpha"
        elif win_rate <= 0.42:
            signal = "drag"
        else:
            signal = "noise"
    else:
        signal = "neutral"

    return ComponentStats(
        name=name,
        total_trades=len(active),
        wins=wins,
        losses=losses,
        avg_r=round(avg_r, 3),
        win_rate=round(win_rate, 4),
        edge=round(edge, 4),
        signal=signal,
    )


def get_component_stats(attribution_rows: list[dict]) -> AttributionInsights:
    """
    Given a list of attribution rows (from the signal_attribution table),
    compute per-component accuracy stats and return an AttributionInsights object.
    """
    if not attribution_rows:
        return AttributionInsights(
            prompt_section="SIGNAL ATTRIBUTION: No attribution data yet — learning phase."
        )

    components = [
        _make_stats("RS Boost",          attribution_rows, "rs_boost"),
        _make_stats("Insider Flow",       attribution_rows, "insider_boost"),
        _make_stats("Options Flow",       attribution_rows, "options_flow_boost"),
        _make_stats("Short Interest",     attribution_rows, "short_interest_boost"),
        _make_stats("Catalyst Stack",     attribution_rows, "catalyst_score"),
        _make_stats("Memory Adjustment",  attribution_rows, "memory_boost"),
    ]

    # Overall stats
    won_all   = sum(1 for r in attribution_rows if r.get("won"))
    total_all = len(attribution_rows)
    overall_wr = won_all / total_all if total_all > 0 else 0.0

    # Best and worst components (min 5 trades)
    ranked = [c for c in components if c.total_trades >= 5]
    best_component  = max(ranked, key=lambda c: c.win_rate).name if ranked else None
    worst_component = min(ranked, key=lambda c: c.win_rate).name if ranked else None

    reliable_count = sum(1 for c in components if c.total_trades >= 10 and c.win_rate >= 0.55)
    noise_count    = sum(1 for c in components if c.total_trades >= 10 and c.win_rate < 0.45)

    # Build the prompt section
    lines = [
        f"SIGNAL ATTRIBUTION ANALYSIS ({total_all} outcomes | overall WR: {overall_wr*100:.0f}%):"
    ]
    for comp in components:
        if comp.total_trades >= 3:
            lines.append(f"  • {comp.summary()}")
    if best_component:
        lines.append(f"  ★ Most reliable signal: {best_component}")
    if worst_component and noise_count > 0:
        lines.append(f"  ⚠ Least reliable signal: {worst_component} — reduce weight")

    return AttributionInsights(
        component_stats=components,
        best_component=best_component,
        worst_component=worst_component,
        reliable_count=reliable_count,
        noise_count=noise_count,
        prompt_section="\n".join(lines),
    )


def build_attribution_prompt_section(insights: AttributionInsights) -> str:
    """Format attribution insights for injection into Claude's learning brief."""
    return insights.prompt_section


# ---------------------------------------------------------------------------
# Supabase reader
# ---------------------------------------------------------------------------

async def load_attribution_rows(supabase, limit: int = 500) -> list[dict]:
    """
    Fetch recent attribution rows from Supabase.
    Returns empty list if the table doesn't exist yet or on any error.
    """
    try:
        res = (
            supabase.table("signal_attribution")
            .select(
                "signal_id,symbol,setup_type,regime,signal_side,"
                "rs_boost,insider_boost,options_flow_boost,short_interest_boost,"
                "memory_boost,catalyst_score,outcome_score,r_multiple,won,"
                "hit_stop,hit_target"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        logger.warning("load_attribution_rows: %s", exc)
        return []
