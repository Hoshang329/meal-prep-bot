"""Daily menu formatting — pure, no LLM (the plan already carries per-meal steps)."""

from __future__ import annotations

from typing import Optional

from src.memory.schema import CurrentPlan, DayPlan, Meal, Profile
from src.util.dt import dow_for, today_iso


def find_today(plan: Optional[CurrentPlan]) -> Optional[DayPlan]:
    if plan is None:
        return None
    today = today_iso()
    for d in plan.days:
        if d.date == today:
            return d
    return None


def find_by_date(plan: Optional[CurrentPlan], date_iso: str) -> Optional[DayPlan]:
    if plan is None:
        return None
    for d in plan.days:
        if d.date == date_iso:
            return d
    return None


def _meal_block(label: str, meal: Optional[Meal], profile: Optional[Profile] = None) -> Optional[str]:
    if not meal:
        return None
    head = f"\n**{label}: {meal.name}**"
    # optional meal-time hint from the profile (the dead ``profile`` param before)
    slot = (meal.slot or label).lower()
    meal_time = profile.meal_times.get(slot) if profile and profile.meal_times else None
    if meal_time:
        head += f" · {meal_time}"
    if meal.time_minutes:
        head += f" ({meal.time_minutes} min)"
    if meal.prep_ahead or meal.reheat:
        head += " — reheat from prep day 🔄"
    parts = [head]
    if meal.ingredients:
        ings = ", ".join(
            f"{i.qty:g} {i.unit or ''} {i.item}".strip() if i.qty else i.item
            for i in meal.ingredients
        )
        parts.append(f"  ingredients: {ings}")
    if meal.steps:
        for i, s in enumerate(meal.steps, 1):
            parts.append(f"  {i}. {s}")
    return "\n".join(parts)


def format_day(day: DayPlan, profile: Optional[Profile] = None) -> str:
    title = f"📅 **{dow_for(day.date)} ({day.date})**"
    if day.is_prep_ahead:
        title += " — mostly prep-ahead"
    blocks = [title]
    for label, meal in (("Breakfast", day.breakfast), ("Lunch", day.lunch), ("Dinner", day.dinner)):
        b = _meal_block(label, meal, profile)
        if b:
            blocks.append(b)
    if day.snacks:
        for s in day.snacks:
            b = _meal_block("Snack", s, profile)
            if b:
                blocks.append(b)
    if day.notes:
        blocks.append(f"\n📝 {day.notes}")
    return "\n".join(blocks)


def format_today(plan: Optional[CurrentPlan], profile: Optional[Profile] = None) -> str:
    day = find_today(plan)
    if day is None:
        return "No plan for today yet. Use /plan to generate this week's menu first."
    return format_day(day, profile)
