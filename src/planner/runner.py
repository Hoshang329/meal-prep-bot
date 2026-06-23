"""High-level plan orchestration shared by onboarding, the /plan command, and the scheduler.

Keeps the "generate → finalise grocery → store → send for approval" sequence in one
place so callers don't re-implement it. Reads everything from the memory store,
calls the planner + grocery layers, persists ``current_plan``, and sends messages
to the owner via the bot client.

If an approved plan already exists, it is archived to ``plan_history`` before
being overwritten by a fresh draft — so regenerating doesn't silently lose the
record of what was approved last week.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.app import ctx
from src.memory.schema import (
    CurrentPlan, Learnings, Pantry, PlanHistoryEntry, Preferences, Prices, Profile,
)
from src.planner import grocery, weekly_plan
from src.chat import components, messaging
from src.util.dt import now_iso

log = logging.getLogger(__name__)


def format_menu(plan: CurrentPlan) -> str:
    cur = plan.currency or ""
    header = f"📋 **Menu for the week of {plan.week_of}**"
    if plan.est_cost is not None:
        budget_s = f" · budget {cur}{plan.budget:.0f}" if plan.budget is not None else ""
        header += f"\n_est. {cur}{plan.est_cost:.0f}{budget_s}_"
    lines = [header]
    for d in plan.days:
        dow = d.dow or ""
        lines.append(f"\n**{dow} ({d.date})**" + (" — prep-ahead" if d.is_prep_ahead else ""))
        for label, meal in (("🌅", d.breakfast), ("🍱", d.lunch), ("🍽", d.dinner)):
            if meal:
                tag = " 🔄" if meal.prep_ahead or meal.reheat else ""
                lines.append(f"  {label} {meal.name}{tag}")
        for s in d.snacks:
            lines.append(f"  🥨 {s.name}")
    return "\n".join(lines)


async def _archive_previous_plan_if_approved() -> None:
    """Append the current approved plan (if any) to plan_history before overwrite."""
    prev = await ctx.store.get(CurrentPlan)
    if prev is None or prev.status != "approved":
        return
    await ctx.store.append_log("plan_history", PlanHistoryEntry(
        week_of=prev.week_of,
        status="archived",  # superseded by a new draft
        cost=prev.est_cost,
        logged_at=now_iso(),
    ))


async def generate_and_store_plan(week_of: Optional[str] = None, changes: str = "") -> CurrentPlan:
    """Generate a plan from current memory, finalise grocery, persist as current_plan."""
    ctx.ensure()
    # archive the previous approved plan so its weekly history isn't lost
    await _archive_previous_plan_if_approved()

    profile = await ctx.store.get_or_default(Profile)
    prefs = await ctx.store.get_or_default(Preferences)
    prices = await ctx.store.get_or_default(Prices)
    pantry = await ctx.store.get_or_default(Pantry)
    learnings = await ctx.store.get_or_default(Learnings)
    feedback = await ctx.store.get_logs("feedback", limit=30)

    plan = await weekly_plan.generate(profile, prefs, prices, pantry, learnings, feedback,
                                      week_of=week_of, changes=changes)
    plan = await grocery.finalize_and_trim(plan, prices, pantry)
    await ctx.store.set(plan)  # writes current_plan to the channel
    return plan


async def send_plan_for_approval(plan: CurrentPlan) -> None:
    ctx.ensure()
    text = format_menu(plan) + (
        "\n\n**Approve** to lock this and generate the grocery list, or "
        "**Request changes** and tell me what to swap."
    )
    await messaging.send_md(ctx.bot, ctx.owner_id, text, view=components.approve_change())


async def send_grocery_list(plan: CurrentPlan) -> None:
    ctx.ensure()
    text = grocery.format(plan) + "\n\n**Got it** to confirm, or **Need changes**."
    await messaging.send_md(ctx.bot, ctx.owner_id, text, view=components.grocery_confirm())
