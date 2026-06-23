"""The "did you cook?" feedback loop.

Evening confirmation ping → user taps Yes / Made something else / Skipped → we log
a ``feedback`` entry (append-only) → the learn job later distils it into learnings.

For "Made something else" / "Skipped" we ask a one-line reason; that reason is
stored on the feedback entry. The pending-reason state lives in ``ctx.flags``
(keyed by owner). The reason flag is also cleared by ``/cancel`` so the user can
escape the follow-up without sending text.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from src.app import ctx
from src.memory.schema import CurrentPlan, FeedbackEntry
from src.planner import daily
from src.chat import components, messaging
from src.util.dt import dow_for, now_iso, today_iso

log = logging.getLogger(__name__)

_REASON_FLAG = "fb_reason"  # ctx.flags key prefix


def _reason_flag_key() -> str:
    return f"{_REASON_FLAG}:{ctx.owner_id}"


async def ask_did_cook(date_iso: Optional[str] = None) -> None:
    """Send the evening "did you cook?" ping for ``date_iso`` (default today)."""
    ctx.ensure()
    date_iso = date_iso or today_iso()
    plan = await ctx.store.get(CurrentPlan)
    day = daily.find_by_date(plan, date_iso) if plan else None
    if day is None:
        # nothing planned that day — skip silently
        return
    summary_bits = []
    for label, meal in (("B", day.breakfast), ("L", day.lunch), ("D", day.dinner)):
        if meal:
            summary_bits.append(f"{label}: {meal.name}")
    summary = " · ".join(summary_bits) or "today's plan"
    text = (
        f"🍲 **Did you cook today's food?** ({dow_for(date_iso)} {date_iso})\n"
        f"Planned: {summary}\n\nTap an option 👇"
    )
    await messaging.send_md(
        ctx.bot, ctx.owner_id, text,
        view=components.feedback_did_cook(date_iso),
    )


async def handle_callback(data: str, interaction: discord.Interaction) -> None:
    """Handle ``fb:<cook|other|skip>:<date>`` callbacks."""
    ctx.ensure()
    prefix, parts = components.parse_callback(data)
    if prefix != "fb" or len(parts) < 2:
        return
    outcome_raw, date_iso = parts[0], parts[1]
    outcome = {"cook": "cooked", "other": "other", "skip": "skipped"}.get(outcome_raw)
    if outcome is None:
        return

    if outcome == "cooked":
        await _log(FeedbackEntry(date=date_iso, outcome="cooked", logged_at=now_iso()))
        await messaging.send_md(ctx.bot, ctx.owner_id, "Nice! Logged ✅")
    else:
        # ask for a one-line reason
        ctx.flags[_reason_flag_key()] = date_iso
        ctx.flags[f"{_reason_flag_key()}:outcome"] = outcome
        prompt = "Made something else" if outcome == "other" else "Skipped"
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            f"{prompt} — got it. What was the reason? (one line, e.g. "
            "'too tired', 'ate out', 'didn't like the plan'). Type it below, "
            "or /cancel to skip.",
        )


async def handle_reason_text(text: str) -> bool:
    """If we're awaiting a feedback reason, log it. Returns True if consumed."""
    ctx.ensure()
    date_iso = ctx.flags.get(_reason_flag_key())
    if not date_iso:
        return False
    outcome = ctx.flags.pop(f"{_reason_flag_key()}:outcome", "other")
    ctx.flags.pop(_reason_flag_key(), None)
    await _log(FeedbackEntry(date=date_iso, outcome=outcome, reason=text.strip(),
                             logged_at=now_iso()))
    await messaging.send_md(ctx.bot, ctx.owner_id, "Logged — I'll learn from that 🙏")
    return True


async def _log(entry: FeedbackEntry) -> None:
    await ctx.store.append_log("feedback", entry)
    log.info("Feedback logged: %s %s -> %s", entry.date, entry.meal, entry.outcome)
