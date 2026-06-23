"""Small datetime helpers driven by the configured TIMEZONE.

Kept here so the rest of the code never calls ``datetime.now()`` ad-hoc and
accidentally uses the server's local time instead of the user's.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import settings

log = logging.getLogger(__name__)

_DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# One-shot flag so the warning fires only once at the first bad call instead of
# spamming every scheduled-job invocation.
_TZ_WARNED = False


def _tz(name: str | None = None) -> ZoneInfo:
    global _TZ_WARNED
    try:
        return ZoneInfo(name or settings.timezone)
    except Exception as e:
        if not _TZ_WARNED:
            log.warning("Unknown timezone %r (%s); falling back to UTC. "
                        "Fix TIMEZONE in config/.env.",
                        name or settings.timezone, e)
            _TZ_WARNED = True
        return ZoneInfo("UTC")


def now_dt(name: str | None = None) -> datetime:
    return datetime.now(_tz(name))


def now_iso(name: str | None = None) -> str:
    return now_dt(name).isoformat(timespec="seconds")


def today_iso(name: str | None = None) -> str:
    return now_dt(name).date().isoformat()


def dow_for(date_iso: str) -> str:
    """Weekday name for an ISO date string."""
    return _DOW[datetime.fromisoformat(date_iso).weekday()]


def monday_of(date_iso: str) -> str:
    """ISO date of the Monday of the week containing ``date_iso``."""
    d = datetime.fromisoformat(date_iso).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def add_days(date_iso: str, days: int) -> str:
    d = datetime.fromisoformat(date_iso).date()
    return (d + timedelta(days=days)).isoformat()


def parse_date(date_iso: str) -> date:
    return datetime.fromisoformat(date_iso).date()
