"""Tests for no_trade_calendar.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from no_trade_calendar import (
    is_fomc_day, is_cpi_day, is_jobs_day, is_market_holiday,
    is_first_minutes, is_last_minutes, is_low_liquidity_session,
    should_trade_now, get_calendar_context,
    FOMC_DATES_2026, CPI_DATES_2026, JOBS_DATES_2026, HOLIDAY_DATES_2026,
)

ET = ZoneInfo("America/New_York")


def _mock_now(dt_str: str):
    """Return a context manager that patches _now_et and _today_et."""
    from unittest.mock import patch
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=ET)
    return patch.multiple(
        "no_trade_calendar",
        _now_et=lambda: dt,
        _today_et=lambda: dt.date(),
    )


class TestEventDates:
    def test_fomc_dates_are_correct_count(self):
        assert len(FOMC_DATES_2026) == 8

    def test_cpi_dates_are_monthly(self):
        assert len(CPI_DATES_2026) == 12

    def test_jobs_dates_are_monthly(self):
        assert len(JOBS_DATES_2026) == 12

    def test_holiday_dates_reasonable(self):
        assert 9 <= len(HOLIDAY_DATES_2026) <= 11

    def test_fomc_day_detected(self):
        with _mock_now("2026-01-28T14:00:00"):
            assert is_fomc_day() is True

    def test_fomc_day_not_detected_on_other_day(self):
        with _mock_now("2026-01-27T14:00:00"):
            assert is_fomc_day() is False

    def test_cpi_day_detected(self):
        with _mock_now("2026-02-13T09:00:00"):
            assert is_cpi_day() is True

    def test_jobs_day_detected(self):
        with _mock_now("2026-01-09T08:00:00"):
            assert is_jobs_day() is True

    def test_market_holiday_detected(self):
        with _mock_now("2026-01-01T12:00:00"):
            assert is_market_holiday() is True

    def test_normal_day_not_holiday(self):
        with _mock_now("2026-01-02T12:00:00"):
            assert is_market_holiday() is False


class TestSessionWindows:
    def test_first_5_minutes_blocked(self):
        with _mock_now("2026-06-01T09:32:00"):
            assert is_first_minutes(5) is True

    def test_after_open_window_allowed(self):
        with _mock_now("2026-06-01T09:36:00"):
            assert is_first_minutes(5) is False

    def test_last_10_minutes_blocked(self):
        with _mock_now("2026-06-01T15:52:00"):
            assert is_last_minutes(10) is True

    def test_before_close_window_allowed(self):
        with _mock_now("2026-06-01T15:49:00"):
            assert is_last_minutes(10) is False

    def test_premarket_blocked(self):
        with _mock_now("2026-06-01T08:00:00"):
            assert is_low_liquidity_session() is True

    def test_midday_allowed(self):
        with _mock_now("2026-06-01T12:00:00"):
            assert is_low_liquidity_session() is False

    def test_after_3_45_blocked(self):
        with _mock_now("2026-06-01T15:46:00"):
            assert is_low_liquidity_session() is True


class TestShouldTradeNow:
    def test_fomc_day_blocks_trading(self):
        with _mock_now("2026-01-28T12:00:00"):
            allowed, reason = should_trade_now()
            assert allowed is False
            assert "FOMC" in reason

    def test_holiday_blocks_trading(self):
        with _mock_now("2026-12-25T10:00:00"):
            allowed, reason = should_trade_now()
            assert allowed is False
            assert "holiday" in reason.lower()

    def test_midday_clear_day_allows_trading(self):
        with _mock_now("2026-06-01T12:00:00"):
            allowed, reason = should_trade_now()
            assert allowed is True
            assert reason == ""

    def test_calendar_context_is_string(self):
        with _mock_now("2026-06-01T12:00:00"):
            ctx = get_calendar_context()
            assert isinstance(ctx, str)
            assert len(ctx) > 10
