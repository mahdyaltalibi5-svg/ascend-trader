"""Tests for catalyst_stack.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from catalyst_stack import (
    build_catalyst_score, catalyst_confidence_boost,
    score_news_sentiment, minimum_catalyst_threshold,
    CatalystScore,
)

def _ind():
    return {"volume_ratio": 1.0, "ema_trend": "neutral", "roc5": 0.0}


class TestNewsSentiment:
    def test_empty_news_neutral(self):
        assert score_news_sentiment([]) == 0.5

    def test_bullish_words_increase_score(self):
        news = [{"headline": "Stock surges on record earnings beat upgrade"}]
        s = score_news_sentiment(news)
        assert s > 0.5

    def test_bearish_words_decrease_score(self):
        news = [{"headline": "Company miss warning downgrade loss sell"}]
        s = score_news_sentiment(news)
        assert s < 0.5

    def test_mixed_is_near_0_5(self):
        news = [{"headline": "stock beat expectations but miss guidance warning"}]
        s = score_news_sentiment(news)
        assert 0.3 <= s <= 0.7


class TestBuildCatalystScore:
    def test_returns_catalyst_score_object(self):
        cs = build_catalyst_score(
            symbol="NVDA",
            ind_1h=_ind(),
            ind_1d=_ind(),
            news=[],
            earnings_intel={},
            institutional_intel={},
            rs_intel={},
            setup_type="breakout",
            regime="trend_day",
        )
        assert isinstance(cs, CatalystScore)

    def test_total_score_bounded(self):
        cs = build_catalyst_score(
            symbol="TSLA",
            ind_1h={**_ind(), "volume_ratio": 4.0, "ema_trend": "bullish"},
            ind_1d={**_ind(), "ema_trend": "bullish"},
            news=[{"headline": "surges record beat upgrade"}],
            earnings_intel={"days_to_earnings": 2},
            institutional_intel={"TSLA": {"flow": "accumulating", "conviction": 0.9}},
            rs_intel={"TSLA": {"rs_signal": "leader", "rs_rank": 0.95}},
            setup_type="breakout",
            regime="trend_day",
        )
        assert 0.0 <= cs.total_score <= 10.0

    def test_high_conviction_setup_fires_catalysts(self):
        cs = build_catalyst_score(
            symbol="NVDA",
            ind_1h={**_ind(), "volume_ratio": 3.5, "ema_trend": "bullish"},
            ind_1d={**_ind(), "ema_trend": "bullish"},
            news=[{"headline": "surges record beat buy rally"}],
            earnings_intel={"days_to_earnings": 1},
            institutional_intel={"NVDA": {"flow": "accumulating", "conviction": 0.85}},
            rs_intel={"NVDA": {"rs_signal": "leader", "rs_rank": 0.92}},
            setup_type="breakout",
            regime="trend_day",
        )
        assert len(cs.fired_catalysts) >= 3
        assert cs.total_score > 5.0

    def test_components_keys_present(self):
        cs = build_catalyst_score("AAPL", _ind(), _ind(), [], {}, {}, {}, "unknown", "unknown")
        expected = {
            "earnings_proximity", "institutional_accumulation", "news_sentiment",
            "volume_catalyst", "momentum_alignment", "setup_quality", "rs_leadership",
        }
        assert set(cs.components.keys()) == expected

    def test_minimum_threshold_is_4(self):
        assert minimum_catalyst_threshold() == 4.0


class TestCatalystConfidenceBoost:
    def _cs(self, score):
        return CatalystScore(
            symbol="X", total_score=score, components={}, fired_catalysts=[],
            dominant_catalyst="", catalyst_note="",
        )

    def test_high_score_boosts(self):
        r = catalyst_confidence_boost(self._cs(7.5), 0.70)
        assert r == pytest.approx(0.78, abs=0.001)

    def test_moderate_score_small_boost(self):
        r = catalyst_confidence_boost(self._cs(5.5), 0.70)
        assert r == pytest.approx(0.74, abs=0.001)

    def test_very_low_score_penalises(self):
        r = catalyst_confidence_boost(self._cs(1.5), 0.70)
        assert r == pytest.approx(0.60, abs=0.001)

    def test_result_bounded(self):
        r1 = catalyst_confidence_boost(self._cs(10.0), 0.98)
        assert r1 <= 1.0
        r2 = catalyst_confidence_boost(self._cs(0.0), 0.05)
        assert r2 >= 0.0
