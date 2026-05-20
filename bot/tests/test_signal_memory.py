"""Tests for signal_memory.py — calibrate_confidence and helpers."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from signal_memory import calibrate_confidence, _bucket_label, _compute_calibration


class TestCalibrateConfidence:
    def test_below_20_samples_unchanged(self):
        # Any sample_size < 20 must not change confidence
        for n in [0, 1, 5, 10, 19]:
            result = calibrate_confidence(0.75, 0.90, n)
            assert result == 0.75, f"n={n}: expected 0.75, got {result}"

    def test_boost_when_history_much_better(self):
        # hist_wr (0.92) >> raw_conf (0.70): delta = +0.22 > 0.15 → boost
        result = calibrate_confidence(0.70, 0.92, 50)
        assert result > 0.70
        assert result <= 0.95

    def test_penalty_when_history_much_worse_mild(self):
        # hist_wr (0.50) << raw_conf (0.75): delta = -0.25 in (-0.30, -0.15) → mild penalty
        result = calibrate_confidence(0.75, 0.50, 50)
        assert result < 0.75

    def test_penalty_when_history_severe(self):
        # hist_wr (0.30) << raw_conf (0.75): delta = -0.45 < -0.30 → severe penalty
        result = calibrate_confidence(0.75, 0.30, 50)
        assert result < 0.75 - 0.10  # bigger penalty than mild case

    def test_no_change_in_neutral_zone(self):
        # hist_wr close to raw_conf: no adjustment
        result = calibrate_confidence(0.70, 0.72, 30)
        assert result == 0.70

    def test_never_exceeds_0_95(self):
        result = calibrate_confidence(0.93, 0.99, 100)
        assert result <= 0.95

    def test_low_weight_for_20_49_samples(self):
        # With 30 samples, adjustment should be half the normal magnitude
        full  = calibrate_confidence(0.70, 0.92, 100)
        half  = calibrate_confidence(0.70, 0.92, 30)
        assert 0.70 < half < full  # some boost but less than full

    def test_severe_penalty_larger_than_mild(self):
        mild   = calibrate_confidence(0.75, 0.58, 60)  # delta = -0.17 → mild
        severe = calibrate_confidence(0.75, 0.40, 60)  # delta = -0.35 → severe
        assert severe < mild


class TestBucketLabel:
    def test_low_confidence_bucket(self):
        assert _bucket_label(0.60) == "0.60-0.69"
        assert _bucket_label(0.65) == "0.60-0.69"

    def test_high_confidence_bucket(self):
        assert _bucket_label(0.90) == "0.90+"
        assert _bucket_label(0.99) == "0.90+"

    def test_mid_bucket(self):
        assert _bucket_label(0.75) == "0.70-0.79"
        assert _bucket_label(0.85) == "0.80-0.89"


class TestComputeCalibration:
    def test_all_wins_gives_1_0(self):
        rows = [{"confidence": 0.75, "return_1d_pct": 2.0}] * 10
        cal = _compute_calibration(rows)
        assert cal.get("0.70-0.79") == 1.0

    def test_all_losses_gives_0_0(self):
        rows = [{"confidence": 0.75, "return_1d_pct": -1.5}] * 10
        cal = _compute_calibration(rows)
        assert cal.get("0.70-0.79") == 0.0

    def test_empty_rows_returns_empty(self):
        cal = _compute_calibration([])
        assert cal == {}
