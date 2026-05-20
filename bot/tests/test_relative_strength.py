"""Tests for relative_strength.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest
from relative_strength import (
    compute_return_pct, compute_rs_score, compute_rs_rank,
    rs_score_boost, build_rs_prompt_section,
)


def _df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": prices})


class TestComputeReturnPct:
    def test_positive_return(self):
        df = _df([100, 102, 104, 106, 108, 110])
        result = compute_return_pct(df, periods=5)
        assert result == pytest.approx(10.0, rel=0.01)

    def test_negative_return(self):
        df = _df([110, 108, 106, 104, 102, 100])
        result = compute_return_pct(df, periods=5)
        assert result == pytest.approx(-9.09, rel=0.01)

    def test_insufficient_data(self):
        df = _df([100, 101])
        assert compute_return_pct(df, periods=5) == 0.0

    def test_empty_df(self):
        assert compute_return_pct(pd.DataFrame(), periods=5) == 0.0


class TestComputeRsScore:
    def test_outperforming(self):
        sym = _df([100, 102, 104, 106, 108, 115])
        spy = _df([100, 101, 102, 103, 104, 105])
        score = compute_rs_score(sym, spy, periods=5)
        assert score > 0

    def test_underperforming(self):
        sym = _df([100, 99, 98, 97, 96, 95])
        spy = _df([100, 101, 102, 103, 104, 105])
        score = compute_rs_score(sym, spy, periods=5)
        assert score < 0

    def test_in_line(self):
        prices = [100, 101, 102, 103, 104, 105]
        sym = _df(prices)
        spy = _df(prices)
        assert compute_rs_score(sym, spy, periods=5) == pytest.approx(0.0, abs=0.01)


class TestComputeRsRank:
    def test_best_symbol_rank_is_1(self):
        all_bars = {
            "SPY":  _df([100]*6),
            "NVDA": _df([100, 102, 104, 106, 108, 120]),  # +20% vs flat SPY
            "TSLA": _df([100, 100, 100, 100, 100, 100]),  # +0%
            "AAPL": _df([100, 99, 98, 97, 96, 95]),       # -5%
        }
        rank = compute_rs_rank("NVDA", all_bars, periods=5)
        assert rank == pytest.approx(1.0)

    def test_worst_symbol_rank_is_low(self):
        all_bars = {
            "SPY":  _df([100]*6),
            "NVDA": _df([100, 102, 104, 106, 108, 120]),
            "TSLA": _df([100, 100, 100, 100, 100, 100]),
            "AAPL": _df([100, 99, 98, 97, 96, 90]),
        }
        rank = compute_rs_rank("AAPL", all_bars, periods=5)
        assert rank < 0.5

    def test_missing_spy_returns_0_5(self):
        all_bars = {"NVDA": _df([100]*6)}
        rank = compute_rs_rank("NVDA", all_bars)
        assert rank == 0.5


class TestRsScoreBoost:
    def test_long_leader_positive_boost(self):
        rs = {"NVDA": {"rs_signal": "leader"}}
        assert rs_score_boost(rs, "NVDA", "buy")   > 0
        assert rs_score_boost(rs, "NVDA", "long")  > 0

    def test_short_laggard_positive_boost(self):
        rs = {"AAPL": {"rs_signal": "laggard"}}
        assert rs_score_boost(rs, "AAPL", "sell")  > 0
        assert rs_score_boost(rs, "AAPL", "short") > 0

    def test_long_laggard_negative(self):
        rs = {"TSLA": {"rs_signal": "laggard"}}
        assert rs_score_boost(rs, "TSLA", "buy") < 0

    def test_short_leader_negative(self):
        rs = {"MSFT": {"rs_signal": "leader"}}
        assert rs_score_boost(rs, "MSFT", "sell") < 0

    def test_neutral_returns_zero(self):
        rs = {"AMD": {"rs_signal": "neutral"}}
        assert rs_score_boost(rs, "AMD", "buy") == 0.0

    def test_missing_symbol_returns_zero(self):
        assert rs_score_boost({}, "MISSING", "buy") == 0.0


class TestBuildRsPromptSection:
    def test_returns_non_empty_string(self):
        rs = {
            "NVDA": {
                "rs_vs_spy_5d": 4.2,
                "rs_vs_sector_5d": 1.8,
                "rs_vs_spy_20d": 6.1,
                "rs_rank": 0.92,
                "rs_trend": "improving",
                "rs_signal": "leader",
                "sector_etf": "SMH",
            }
        }
        text = build_rs_prompt_section(rs, "NVDA")
        assert "NVDA" in text
        assert "MARKET LEADER" in text
        assert "SPY" in text
