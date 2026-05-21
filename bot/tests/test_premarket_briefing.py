"""Tests for premarket_briefing.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from premarket_briefing import _compute_gap


def test_compute_gap_uses_latest_premarket_price():
    bars = [
        {"o": 100, "c": 101, "v": 1000},
        {"o": 101, "c": 106, "v": 2500},
    ]

    result = _compute_gap(bars, prev_close=100)

    assert result["gap_pct"] == 6.0
    assert result["premarket_price"] == 106
    assert result["premarket_vol"] == 3500


def test_compute_gap_handles_empty_bars():
    assert _compute_gap([], prev_close=100) == {"gap_pct": 0.0, "premarket_vol": 0}
