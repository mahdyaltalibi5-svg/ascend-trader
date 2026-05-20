"""Tests for setup_classifier.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from setup_classifier import (
    classify_setup, score_setup_quality, setup_notes_for_claude,
    SETUP_BREAKOUT, SETUP_GAP_AND_GO, SETUP_MEAN_REVERSION,
    SETUP_EARNINGS_DRIFT, SETUP_PULLBACK_CONTINUATION, SETUP_UNKNOWN,
    SETUP_NEWS_MOMENTUM,
)

def _base_ind():
    return {
        "rsi": 50.0,
        "macd_hist": 0.0,
        "bb_pct": 0.5,
        "volume_ratio": 1.0,
        "roc5": 0.0,
        "stoch_k": 50.0,
        "ema_trend": "neutral",
        "close": 100.0,
        "open": 99.0,
        "prev_close": 98.0,
        "atr": 1.0,
        "ema21": 100.0,
        "ema50": 100.0,
    }


class TestClassifySetup:
    def test_earnings_drift_when_catalyst(self):
        ind = _base_ind()
        setup, conf = classify_setup(ind, ind, ind, [], earnings_catalyst=True)
        assert setup == SETUP_EARNINGS_DRIFT
        assert conf >= 0.8

    def test_breakout_on_high_volume_and_bb(self):
        ind = _base_ind()
        ind5m = {**ind, "volume_ratio": 2.5, "bb_pct": 0.95, "roc5": 0.8}
        setup, conf = classify_setup(ind5m, ind, ind, [])
        assert setup == SETUP_BREAKOUT
        assert conf >= 0.5

    def test_gap_and_go_on_large_gap(self):
        ind_1d = {**_base_ind(), "open": 105.0, "prev_close": 100.0, "close": 106.0}
        ind_1h = {**_base_ind(), "volume_ratio": 2.0}
        setup, conf = classify_setup(_base_ind(), ind_1h, ind_1d, [])
        assert setup == SETUP_GAP_AND_GO

    def test_mean_reversion_on_extreme_rsi(self):
        ind5m = {**_base_ind(), "rsi": 22.0, "bb_pct": 0.02, "stoch_k": 12.0}
        setup, conf = classify_setup(ind5m, _base_ind(), _base_ind(), [])
        assert setup == SETUP_MEAN_REVERSION

    def test_news_momentum_on_many_news(self):
        ind_1h = {**_base_ind(), "volume_ratio": 2.0, "roc5": 0.6}
        news = [{"headline": "stock surges"} for _ in range(5)]
        setup, conf = classify_setup(_base_ind(), ind_1h, _base_ind(), news)
        assert setup == SETUP_NEWS_MOMENTUM

    def test_unknown_on_flat_indicators(self):
        ind = _base_ind()
        # Very flat, no strong signal
        ind["rsi"] = 50
        ind["volume_ratio"] = 1.0
        ind["bb_pct"] = 0.5
        setup, conf = classify_setup(ind, ind, ind, [])
        # Should be unknown or any low-conviction setup
        assert conf <= 0.75  # not high confidence

    def test_confidence_bounded(self):
        ind = _base_ind()
        for earnings_cat in [True, False]:
            _, conf = classify_setup(ind, ind, ind, [], earnings_catalyst=earnings_cat)
            assert 0.0 <= conf <= 1.0

    def test_all_setup_notes_are_strings(self):
        from setup_classifier import ALL_SETUP_TYPES
        for stype in ALL_SETUP_TYPES:
            note = setup_notes_for_claude(stype)
            assert isinstance(note, str)
            assert len(note) > 50


class TestScoreSetupQuality:
    def test_breakout_in_trend_day_is_high_quality(self):
        ind_1h = {**_base_ind(), "volume_ratio": 2.5, "bb_pct": 0.93, "macd_hist": 0.5, "roc5": 0.8}
        q = score_setup_quality(SETUP_BREAKOUT, ind_1h, "trend_day")
        assert q >= 0.60

    def test_breakout_in_chop_range_is_penalised(self):
        ind_1h = {**_base_ind(), "volume_ratio": 2.5}
        q = score_setup_quality(SETUP_BREAKOUT, ind_1h, "chop_range")
        assert q <= 0.50

    def test_mean_reversion_in_chop_range_is_ok(self):
        ind_1h = {**_base_ind(), "rsi": 25.0, "bb_pct": 0.02, "stoch_k": 10.0}
        q = score_setup_quality(SETUP_MEAN_REVERSION, ind_1h, "chop_range")
        assert q >= 0.50

    def test_quality_bounded(self):
        ind_1h = _base_ind()
        for stype in [SETUP_BREAKOUT, SETUP_MEAN_REVERSION, SETUP_PULLBACK_CONTINUATION]:
            q = score_setup_quality(stype, ind_1h, "trend_day")
            assert 0.0 <= q <= 1.0
