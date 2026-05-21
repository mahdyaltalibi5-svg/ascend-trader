"""Tests for signal_attribution.py — build_attribution_row, get_component_stats."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from signal_attribution import (
    build_attribution_row,
    get_component_stats,
    ComponentStats,
    AttributionInsights,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _signal(overrides=None):
    base = {
        "id": "sig-001",
        "symbol": "NVDA",
        "signal": "buy",
        "strength": 0.72,
        "confidence": 0.78,
        "indicators": {
            "rs_boost": 0.08,
            "insider_boost": 0.04,
            "options_flow_boost": 0.06,
            "short_interest_boost": 0.02,
            "confidence_raw": 0.60,
            "catalyst": {"total_score": 0.75},
            "setup": {
                "type": "breakout",
                "memory_adjustment": {"boost_amount": 0.03},
            },
        },
    }
    if overrides:
        base.update(overrides)
    return base


def _outcome(overrides=None):
    base = {
        "signal_id": "sig-001",
        "outcome_score": 1.5,
        "r_multiple": 1.2,
        "hit_stop": False,
        "hit_take_profit": True,
        "return_1d_pct": 0.04,
    }
    if overrides:
        base.update(overrides)
    return base


# ── build_attribution_row ────────────────────────────────────────────────────

class TestBuildAttributionRow:
    def test_basic_structure(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome())
        assert row["signal_id"] == "sig-001"
        assert row["symbol"] == "NVDA"
        assert row["signal_side"] == "buy"

    def test_boost_values_extracted(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome())
        assert abs(row["rs_boost"] - 0.08) < 1e-6
        assert abs(row["insider_boost"] - 0.04) < 1e-6
        assert abs(row["options_flow_boost"] - 0.06) < 1e-6

    def test_won_true_when_hit_target(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome())
        assert row["won"] is True
        assert row["hit_target"] is True

    def test_won_false_when_stop_hit(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome({"hit_stop": True, "hit_take_profit": False, "outcome_score": -0.5}))
        assert row["won"] is False
        assert row["hit_stop"] is True

    def test_boost_fallback_to_top_level(self):
        # If indicators dict is missing the boost, fall back to top-level signal field
        sig = _signal()
        sig["indicators"].pop("rs_boost")
        sig["rs_boost"] = 0.10
        row = build_attribution_row("sig-001", "NVDA", sig, _outcome())
        assert abs(row["rs_boost"] - 0.10) < 1e-6

    def test_zero_boost_when_missing(self):
        sig = _signal()
        sig["indicators"] = {}  # strip all boosts
        row = build_attribution_row("sig-001", "NVDA", sig, _outcome())
        assert row["rs_boost"] == 0.0
        assert row["insider_boost"] == 0.0

    def test_memory_boost_extracted(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome())
        assert abs(row["memory_boost"] - 0.03) < 1e-6

    def test_catalyst_score_extracted(self):
        row = build_attribution_row("sig-001", "NVDA", _signal(), _outcome())
        assert abs(row["catalyst_score"] - 0.75) < 1e-6


# ── get_component_stats ───────────────────────────────────────────────────────

def _make_rows(n_win: int, n_lose: int, rs_boost: float = 0.08, opts_boost: float = 0.0) -> list[dict]:
    """Build synthetic attribution rows with controlled win/loss counts."""
    rows = []
    for _ in range(n_win):
        rows.append({"won": True, "rs_boost": rs_boost, "options_flow_boost": opts_boost, "r_multiple": 1.0,
                     "insider_boost": 0.0, "short_interest_boost": 0.0, "memory_boost": 0.0, "catalyst_score": 0.0})
    for _ in range(n_lose):
        rows.append({"won": False, "rs_boost": rs_boost, "options_flow_boost": opts_boost, "r_multiple": -1.0,
                     "insider_boost": 0.0, "short_interest_boost": 0.0, "memory_boost": 0.0, "catalyst_score": 0.0})
    return rows


class TestGetComponentStats:
    def test_empty_input(self):
        result = get_component_stats([])
        assert isinstance(result, AttributionInsights)
        assert "No attribution data" in result.prompt_section

    def test_overall_win_rate_in_prompt(self):
        rows = _make_rows(7, 3)
        result = get_component_stats(rows)
        assert "70%" in result.prompt_section

    def test_alpha_signal_when_high_winrate(self):
        rows = _make_rows(12, 3, rs_boost=0.08)  # 80% WR → alpha
        result = get_component_stats(rows)
        rs = next(c for c in result.component_stats if c.name == "RS Boost")
        assert rs.signal == "alpha"

    def test_drag_signal_when_low_winrate(self):
        rows = _make_rows(3, 12, rs_boost=0.08)  # 20% WR → drag
        result = get_component_stats(rows)
        rs = next(c for c in result.component_stats if c.name == "RS Boost")
        assert rs.signal == "drag"

    def test_neutral_when_insufficient_data(self):
        rows = _make_rows(3, 3, rs_boost=0.08)  # only 6 trades → neutral
        result = get_component_stats(rows)
        rs = next(c for c in result.component_stats if c.name == "RS Boost")
        assert rs.signal == "neutral"

    def test_best_component_identified(self):
        rs_rows = _make_rows(10, 1, rs_boost=0.08, opts_boost=0.0)
        opts_rows = _make_rows(3, 10, rs_boost=0.0, opts_boost=0.06)
        rows = rs_rows + opts_rows
        result = get_component_stats(rows)
        assert result.best_component == "RS Boost"

    def test_component_summary_sufficient_data(self):
        rows = _make_rows(8, 4, rs_boost=0.08)
        result = get_component_stats(rows)
        rs = next(c for c in result.component_stats if c.name == "RS Boost")
        summary = rs.summary()
        assert "RS Boost" in summary
        assert "WR" in summary

    def test_component_summary_insufficient_data(self):
        rows = _make_rows(2, 1, rs_boost=0.08)
        result = get_component_stats(rows)
        rs = next(c for c in result.component_stats if c.name == "RS Boost")
        assert "insufficient data" in rs.summary()
