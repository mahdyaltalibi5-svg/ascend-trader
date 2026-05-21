"""Tests for regime_brain.py — VIXY proxy signal and classify_regime."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pandas as pd
import numpy as np

from regime_brain import _vixy_signal, classify_regime


# ── VIXY bar builder ──────────────────────────────────────────────────────────

def _vixy_bars(last_close: float, baseline: float = 20.0, n: int = 25) -> pd.DataFrame:
    """
    Build a 'n'-row VIXY bar DataFrame where the first (n-1) bars close at
    'baseline' and the final bar closes at 'last_close'.

    With n=25 and rolling(20), the MA20 at position -1 covers bars[-20:],
    so 19 bars at 'baseline' and 1 bar at 'last_close'.
    MA20 = (19 * baseline + last_close) / 20
    ratio = last_close / MA20
    """
    closes = [baseline] * (n - 1) + [last_close]
    return pd.DataFrame({"close": closes, "high": closes, "low": closes, "open": closes, "volume": [1_000_000] * n})


def _ratio(last_close: float, baseline: float = 20.0) -> float:
    """Expected ratio for the bar structure created by _vixy_bars."""
    ma = (19 * baseline + last_close) / 20
    return last_close / ma


# ── _vixy_signal ──────────────────────────────────────────────────────────────

class TestVixySignal:
    def test_panic_when_ratio_above_1_40(self):
        # Solve: last_close so that ratio >= 1.40
        # ratio = last_close / ((19*20 + last_close)/20) >= 1.40
        # → last_close >= 28.6; use 30.0
        bars = _vixy_bars(30.0, 20.0)
        signal, ratio = _vixy_signal(bars)
        assert signal == "panic", f"ratio={ratio:.3f}"
        assert ratio >= 1.40

    def test_elevated_when_ratio_1_15_to_1_40(self):
        # 24.0 → ratio ≈ 1.19 (see _ratio helper)
        bars = _vixy_bars(24.0, 20.0)
        signal, ratio = _vixy_signal(bars)
        assert signal == "elevated", f"ratio={ratio:.3f}"
        assert 1.10 < ratio < 1.40

    def test_suppressed_when_ratio_below_0_85(self):
        # 16.0 → ratio ≈ 0.81
        bars = _vixy_bars(16.0, 20.0)
        signal, ratio = _vixy_signal(bars)
        assert signal == "suppressed", f"ratio={ratio:.3f}"
        assert ratio < 0.85

    def test_normal_when_close_to_ma(self):
        # 20.8 → ratio ≈ 1.04 — squarely in the normal band
        bars = _vixy_bars(20.8, 20.0)
        signal, ratio = _vixy_signal(bars)
        assert signal == "normal", f"ratio={ratio:.3f}"

    def test_empty_dataframe_returns_normal(self):
        signal, ratio = _vixy_signal(pd.DataFrame())
        assert signal == "normal"
        assert ratio == 1.0

    def test_fewer_than_21_bars_returns_normal(self):
        # Only 15 bars → < 21, so we can't compute a reliable MA
        bars = _vixy_bars(30.0, 20.0, n=15)
        signal, ratio = _vixy_signal(bars)
        assert signal == "normal"
        assert ratio == 1.0


# ── classify_regime VIXY integration ─────────────────────────────────────────

def _empty_df() -> pd.DataFrame:
    """Empty DataFrame accepted by classify_regime's safe helpers."""
    return pd.DataFrame()


def _spy_df(n: int = 60) -> pd.DataFrame:
    """Minimal SPY-like DataFrame for classify_regime."""
    closes = list(np.linspace(480, 500, n))
    highs  = [c * 1.002 for c in closes]
    lows   = [c * 0.998 for c in closes]
    return pd.DataFrame({
        "close": closes,
        "high": highs,
        "low": lows,
        "open": closes,
        "volume": [50_000_000] * n,
    })


class TestClassifyRegimeVixy:
    def test_vixy_signal_propagated_to_result(self):
        spy = _spy_df()
        vixy_bars = _vixy_bars(30.0, 20.0)  # panic → ratio ≈ 1.46
        result = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df(), vixy_bars=vixy_bars)
        assert result.get("vix_proxy_signal") == "panic"
        assert result.get("vix_proxy_ratio", 1.0) >= 1.40

    def test_suppressed_vixy_in_result(self):
        spy = _spy_df()
        vixy_bars = _vixy_bars(16.0, 20.0)  # suppressed
        result = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df(), vixy_bars=vixy_bars)
        assert result.get("vix_proxy_signal") == "suppressed"

    def test_no_vixy_does_not_crash(self):
        spy = _spy_df()
        result = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df())
        assert "regime" in result

    def test_result_has_required_keys(self):
        spy = _spy_df()
        result = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df())
        assert "regime" in result
        assert "confidence" in result

    def test_panic_vixy_increases_volatility_signal(self):
        spy = _spy_df()
        vixy_panic  = _vixy_bars(30.0, 20.0)   # panic
        vixy_normal = _vixy_bars(20.5, 20.0)   # normal

        result_panic  = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df(), vixy_bars=vixy_panic)
        result_normal = classify_regime(spy, _empty_df(), _empty_df(), _empty_df(), _empty_df(), vixy_bars=vixy_normal)

        # With panic VIXY the bot should be more wary — check vix_proxy_ratio differs
        assert result_panic.get("vix_proxy_ratio", 1.0) > result_normal.get("vix_proxy_ratio", 1.0)
