"""
no_trade_calendar.py — Knows when NOT to trade.

Avoiding bad trades is alpha. This module gates all bot activity against
economic event dates, market holidays, and session timing windows.

Uses only Python stdlib — no external calendar APIs required.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# 2026 Economic event calendars
# ---------------------------------------------------------------------------

# FOMC meeting decision days (second day of each 2-day meeting).
# Source: Federal Reserve 2026 FOMC calendar.
FOMC_DATES_2026: list[str] = [
    "2026-01-28",  # Jan 27-28
    "2026-03-18",  # Mar 17-18
    "2026-04-29",  # Apr 28-29
    "2026-06-17",  # Jun 16-17
    "2026-07-29",  # Jul 28-29
    "2026-09-16",  # Sep 15-16
    "2026-10-28",  # Oct 27-28
    "2026-12-09",  # Dec 8-9
]

# CPI release dates — BLS typically releases around 8:30 AM ET.
# Source: BLS CPI release schedule.
CPI_DATES_2026: list[str] = [
    "2026-01-13",  # Dec 2025 CPI
    "2026-02-13",  # Jan 2026 CPI
    "2026-03-11",  # Feb 2026 CPI
    "2026-04-10",  # Mar 2026 CPI
    "2026-05-12",  # Apr 2026 CPI
    "2026-06-10",  # May 2026 CPI
    "2026-07-14",  # Jun 2026 CPI
    "2026-08-12",  # Aug
    "2026-09-11",  # Aug 2026 CPI
    "2026-10-14",  # Oct
    "2026-11-10",  # Oct 2026 CPI
    "2026-12-10",  # Nov 2026 CPI
]

# Non-Farm Payrolls — BLS releases first Friday of each month at 8:30 AM ET.
JOBS_DATES_2026: list[str] = [
    "2026-01-09",  # Jan (first Friday)
    "2026-02-06",  # Feb
    "2026-03-06",  # Mar
    "2026-04-03",  # Apr
    "2026-05-01",  # May
    "2026-06-05",  # Jun
    "2026-07-10",  # Jul (Jul 4 holiday pushes it)
    "2026-08-07",  # Aug
    "2026-09-04",  # Sep
    "2026-10-02",  # Oct
    "2026-11-06",  # Nov
    "2026-12-04",  # Dec
]

# US equity market holidays 2026 (NYSE observed schedule).
HOLIDAY_DATES_2026: list[str] = [
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day (3rd Monday Jan)
    "2026-02-16",  # Presidents' Day (3rd Monday Feb)
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day (last Monday May)
    "2026-06-19",  # Juneteenth National Independence Day
    "2026-07-03",  # Independence Day observed (Jul 4 is Saturday → Friday off)
    "2026-09-07",  # Labor Day (1st Monday Sep)
    "2026-11-26",  # Thanksgiving Day (4th Thursday Nov)
    "2026-12-25",  # Christmas Day
]

EARLY_CLOSE_DATES_2026: list[str] = [
    "2026-07-02",  # Independence Day observed early close
    "2026-11-27",  # Day after Thanksgiving
    "2026-12-24",  # Christmas Eve
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today_et() -> date:
    """Return today's date in Eastern Time."""
    return datetime.now(tz=ET).date()


def _now_et() -> datetime:
    """Return the current datetime in Eastern Time."""
    return datetime.now(tz=ET)


def _parse_dates(date_list: list[str]) -> set[date]:
    return {date.fromisoformat(d) for d in date_list}


_FOMC_SET: set[date] = _parse_dates(FOMC_DATES_2026)
_CPI_SET: set[date] = _parse_dates(CPI_DATES_2026)
_JOBS_SET: set[date] = _parse_dates(JOBS_DATES_2026)
_HOLIDAY_SET: set[date] = _parse_dates(HOLIDAY_DATES_2026)
_EARLY_CLOSE_SET: set[date] = _parse_dates(EARLY_CLOSE_DATES_2026)


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def is_fomc_day() -> bool:
    """True if today is an FOMC meeting decision day."""
    return _today_et() in _FOMC_SET


def is_cpi_day() -> bool:
    """True if today is a CPI release day."""
    return _today_et() in _CPI_SET


def is_jobs_day() -> bool:
    """True if today is a Non-Farm Payrolls release day."""
    return _today_et() in _JOBS_SET


def is_market_holiday() -> bool:
    """True if today is a US market holiday (NYSE closed)."""
    return _today_et() in _HOLIDAY_SET


def is_early_close_day() -> bool:
    """True if today is a known US equity market early-close session."""
    return _today_et() in _EARLY_CLOSE_SET


def is_low_liquidity_session() -> bool:
    """
    True if the current time is outside regular trading hours or in the
    low-liquidity window after 3:45 PM ET.

    Regular session: 9:30 AM – 4:00 PM ET.
    Low-liquidity window: pre-market (before 9:30) OR after 3:45 PM.
    """
    now = _now_et()
    current_time = now.time()
    market_open = time(9, 30)
    low_liq_start = time(12, 45) if is_early_close_day() else time(15, 45)

    if current_time < market_open:
        return True  # Pre-market
    if current_time >= low_liq_start:
        return True  # After 3:45 PM (includes after-hours)
    return False


def is_first_minutes(buffer_minutes: int = 5) -> bool:
    """
    True if we are within the first `buffer_minutes` of the market open
    (default: 9:30–9:35 ET). Opening range is noisy — avoid entries.
    """
    now = _now_et()
    current_time = now.time()
    open_start = time(9, 30)
    open_end = time(9, 30 + buffer_minutes) if buffer_minutes < 30 else time(10, 0)
    # Handle the case where buffer_minutes pushes minutes past 59
    open_hour = 9
    open_minute = 30 + buffer_minutes
    if open_minute >= 60:
        open_hour += 1
        open_minute -= 60
    open_end = time(open_hour, open_minute)
    return open_start <= current_time < open_end


def is_last_minutes(buffer_minutes: int = 10) -> bool:
    """
    True if we are within the last `buffer_minutes` before market close
    (default: 3:50–4:00 ET). Closing imbalances create erratic price action.
    """
    now = _now_et()
    current_time = now.time()
    close_hour = 16
    close_minute = 0
    start_minute = close_minute - buffer_minutes
    start_hour = close_hour
    if start_minute < 0:
        start_hour -= 1
        start_minute += 60
    close_buffer_start = time(start_hour, start_minute)
    market_close = time(close_hour, close_minute)
    return close_buffer_start <= current_time < market_close


# ---------------------------------------------------------------------------
# Composite logic
# ---------------------------------------------------------------------------

def get_no_trade_reason() -> str | None:
    """
    Return a human-readable reason why we should not trade right now,
    or None if conditions are clear.

    Checks are evaluated in priority order — the most dangerous condition
    is returned first.
    """
    if is_market_holiday():
        return "Market is closed today (US market holiday)."
    if is_early_close_day() and _now_et().time() >= time(12, 45):
        return "Market early-close day after 12:45 PM ET — liquidity fading, spreads can widen."
    if is_fomc_day():
        return "FOMC decision day — expect extreme volatility around announcement; avoid trading."
    if is_cpi_day():
        return "CPI release day — inflation data creates whipsaw moves; use extra caution."
    if is_jobs_day():
        return "Non-Farm Payrolls day — macro event risk elevated; avoid early session trades."
    if is_low_liquidity_session():
        now = _now_et()
        if now.time() < time(9, 30):
            return "Pre-market session — liquidity is thin and spreads are wide."
        return "Late session after 3:45 PM ET — liquidity fading, closing imbalances likely."
    if is_first_minutes():
        return "First 5 minutes of market open — price discovery is erratic; wait for settle."
    if is_last_minutes():
        return "Last 10 minutes before close — closing auction imbalances create noise."
    return None


def should_trade_now() -> tuple[bool, str]:
    """
    Primary gate: returns (True, "") if trading is allowed,
    or (False, reason_string) if it is not.
    """
    reason = get_no_trade_reason()
    if reason is None:
        return True, ""
    return False, reason


def get_calendar_context() -> str:
    """
    Generate a concise context string suitable for injection into a Claude
    prompt so the model is aware of macro calendar conditions.

    Example output:
        "Today is CPI release day. Extra caution required. Expect elevated
         volatility and potential fakeouts."
    """
    today = _today_et()
    now = _now_et()
    lines: list[str] = []

    if is_market_holiday():
        lines.append("Today is a US market holiday — markets are closed.")
        return " ".join(lines)

    # Date-based events
    if is_fomc_day():
        lines.append(
            "Today is an FOMC meeting decision day. "
            "The Fed will announce its rate decision — expect sharp, unpredictable moves "
            "around the release time (~2:00 PM ET). Strict risk management required."
        )
    if is_cpi_day():
        lines.append(
            "Today is a CPI release day. "
            "Extra caution required. Expect elevated volatility and potential fakeouts "
            "immediately following the 8:30 AM ET data print."
        )
    if is_jobs_day():
        lines.append(
            "Today is Non-Farm Payrolls day. "
            "The jobs report (8:30 AM ET) creates macro event risk; "
            "avoid entering positions before the data clears."
        )

    # Session-based conditions
    if now.time() < time(9, 30):
        lines.append(
            "We are currently in the pre-market session. "
            "Liquidity is thin and bid-ask spreads are wide — do not trade."
        )
    elif is_first_minutes(5):
        lines.append(
            "We are in the opening 5-minute window (9:30–9:35 ET). "
            "Price discovery is volatile; wait for the initial range to form."
        )
    elif is_last_minutes(10):
        lines.append(
            "We are in the last 10 minutes before market close. "
            "Closing imbalances and MOC orders create erratic price action — avoid new entries."
        )
    elif now.time() >= time(15, 45):
        lines.append(
            "We are past 3:45 PM ET. "
            "Liquidity is fading — use caution with any open positions."
        )

    if not lines:
        lines.append(
            f"No major macro events today ({today.strftime('%A, %B %d, %Y')}). "
            "Calendar is clear — normal trading rules apply."
        )

    return " ".join(lines)
