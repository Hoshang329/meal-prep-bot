"""Weekly plan generation: LLM produces a Monday–Sunday CurrentPlan (menu + grocery list)."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import ValidationError

from src.memory.schema import (
    CurrentPlan, FeedbackEntry, Learnings, Pantry, Preferences, Prices, Profile,
)
from src.planner import llm, prompts
from src.util.dt import add_days, now_iso

log = logging.getLogger(__name__)


def week_dates(week_of: str) -> list[str]:
    """The 7 ISO dates (Mon..Sun) for a plan whose Monday is ``week_of``."""
    return [add_days(week_of, i) for i in range(7)]


def _coerce_plan(data: dict, week_of: str, profile: Profile) -> CurrentPlan:
    """Fill server-controlled fields, then validate into CurrentPlan."""
    data = dict(data)
    data.setdefault("week_of", week_of)
    data.setdefault("status", "draft")
    data.setdefault("generated_at", now_iso())
    data.setdefault("currency", profile.currency)
    if profile.weekly_budget is not None:
        data.setdefault("budget", profile.weekly_budget)
    # stamp day dates/dow if the model left them blank
    dates = week_dates(week_of)
    days = data.get("days") or []
    for i, d in enumerate(days):
        if i < len(dates):
            d.setdefault("date", dates[i])
            if not d.get("dow"):
                from src.util.dt import dow_for
                d["dow"] = dow_for(dates[i])
    data["days"] = days
    try:
        return CurrentPlan.model_validate(data)
    except ValidationError as e:
        log.error("Weekly plan failed validation: %s\nRaw: %s", e, str(data)[:500])
        raise llm.LLMError(f"Weekly plan did not match schema: {e}") from e


async def generate(
    profile: Profile,
    preferences: Preferences,
    prices: Prices,
    pantry: Pantry,
    learnings: Learnings,
    feedback: list[FeedbackEntry],
    *,
    week_of: Optional[str] = None,
    changes: str = "",
) -> CurrentPlan:
    """Generate next week's plan. ``week_of`` defaults to the Monday of today."""
    from src.util.dt import monday_of, today_iso
    week_of = week_of or monday_of(today_iso())
    dates = week_dates(week_of)
    household = profile.household_size or 1

    messages = prompts.build_weekly_plan_messages(
        profile, preferences, prices, pantry, learnings, feedback, week_of, dates, household, changes
    )
    data = await llm.chat_json(messages, CurrentPlan, temperature=0.7)
    return _coerce_plan(data, week_of, profile)
