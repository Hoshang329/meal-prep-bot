"""Unit tests for datetime helpers (deterministic functions; now/today are tz-driven)."""

from datetime import date, datetime

from src.util import dt
from src.planner.weekly_plan import week_dates


# 2026-06-22 is a Monday — used as a stable anchor for all date math below.
MON = "2026-06-22"


def test_dow_for_monday():
    assert dt.dow_for(MON) == "Monday"


def test_dow_for_sunday():
    assert dt.dow_for("2026-06-28") == "Sunday"  # Mon + 6


def test_dow_for_tuesday():
    assert dt.dow_for("2026-06-23") == "Tuesday"


def test_monday_of_a_monday_is_itself():
    assert dt.monday_of(MON) == MON


def test_monday_of_midweek():
    assert dt.monday_of("2026-06-25") == MON  # Thursday -> same week's Monday


def test_monday_of_sunday():
    assert dt.monday_of("2026-06-28") == MON  # Sunday -> same week's Monday


def test_add_days_forward():
    assert dt.add_days(MON, 1) == "2026-06-23"
    assert dt.add_days(MON, 7) == "2026-06-29"


def test_add_days_backward():
    assert dt.add_days(MON, -1) == "2026-06-21"


def test_parse_date():
    assert dt.parse_date(MON) == date(2026, 6, 22)


def test_week_dates_seven_days_mon_to_sun():
    days = week_dates(MON)
    assert days == ["2026-06-22", "2026-06-23", "2026-06-24",
                    "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"]


# ─── now/today (tz-driven; assert shape only) ─────────────────────────────────


def test_now_iso_is_parseable():
    s = dt.now_iso()
    assert isinstance(s, str)
    datetime.fromisoformat(s)  # raises if malformed


def test_today_iso_is_date_string():
    s = dt.today_iso()
    assert len(s) == 10
    assert s[4] == "-" and s[7] == "-"
    datetime.fromisoformat(s)
