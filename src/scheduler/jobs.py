"""Scheduled cron jobs: weekly plan, daily menu, prep-day list, feedback ping, learn.

The scheduler (set up in ``main.py``) fires these at times derived from the
``CRON_*`` env vars (sensible defaults) in the configured timezone. Each job is
defensive: if onboarding isn't done, no owner has talked to the bot yet, or
there's no current plan, the job logs and returns instead of raising — a nightly
ping must never crash the runner.

Note on the weekly job vs ``/plan``: the manual ``/plan`` command (re)plans the
*current* week (Monday of today). The scheduled weekly job plans the *upcoming*
Monday's week, so a Sunday-morning fire prepares the week that starts the next
day — which is what you want from automation.
"""

from __future__ import annotations

import functools
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.triggers.cron import CronTrigger

from src.app import ctx
from src.config import settings
from src.feedback import learn as learn_mod
from src.feedback import loop as feedback
from src.memory.schema import CurrentPlan, OnboardingState, Profile
from src.planner import daily, prep_day, runner
from src.chat import messaging
from src.util.dt import dow_for, today_iso

log = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _safe(fn):
    """Wrap a job so an exception is logged, not propagated (APScheduler-safe)."""

    @functools.wraps(fn)
    async def wrapper(*args, **kw):
        try:
            await fn(*args, **kw)
        except Exception as e:
            log.exception("Scheduled job %s failed: %s", fn.__name__, e)

    return wrapper


async def _plan_ready() -> bool:
    """True if a plan-dependent job should run: ctx wired, owner known, onboarded."""
    ctx.ensure()
    if ctx.owner_id is None:
        log.info("Skipping scheduled job — no owner yet (send /start first).")
        return False
    st = await ctx.store.get(OnboardingState)
    if st is None or st.phase != "done":
        log.info("Skipping scheduled job — onboarding not complete.")
        return False
    return True


def _next_monday(today: str) -> str:
    """ISO date of the upcoming Monday (inclusive: today if today is Monday)."""
    d = datetime.fromisoformat(today).date()
    days_ahead = (0 - d.weekday()) % 7  # Mon→0, Tue→6, …, Sun→1
    return (d + timedelta(days=days_ahead)).isoformat()


def _time_env(name: str, default_hm: str) -> tuple[int, int]:
    """Parse a ``HH:MM`` env var, falling back to ``default_hm``."""
    raw = os.environ.get(name, "").strip()
    if raw and ":" in raw:
        h, m = raw.split(":", 1)
        try:
            return int(h), int(m)
        except ValueError:
            log.warning("Bad %s=%r, using default %s.", name, raw, default_hm)
    h, m = default_hm.split(":")
    return int(h), int(m)


# ─── jobs ────────────────────────────────────────────────────────────────────


@_safe
async def job_weekly_plan() -> None:
    if not await _plan_ready():
        return
    week_of = _next_monday(today_iso())
    await messaging.send_md(ctx.bot, ctx.owner_id,
                            f"🧠 Planning your week of {week_of}…")
    plan = await runner.generate_and_store_plan(week_of=week_of)
    # generate_and_store_plan now also archives the previous approved plan to
    # plan_history before overwriting — so weekly auto-replans keep a record.
    await runner.send_plan_for_approval(plan)


@_safe
async def job_send_today() -> None:
    if not await _plan_ready():
        return
    plan = await ctx.store.get(CurrentPlan)
    if plan is None:
        return
    day = daily.find_today(plan)
    if day is None:
        # outside the plan's Mon–Sun window — stay silent
        return
    profile = await ctx.store.get_or_default(Profile)
    await messaging.send_md(ctx.bot, ctx.owner_id, daily.format_day(day, profile))


@_safe
async def job_send_prep_day() -> None:
    if not await _plan_ready():
        return
    profile = await ctx.store.get_or_default(Profile)
    prep_dow = profile.prep_day or "Sunday"
    if dow_for(today_iso()) != prep_dow:
        return  # only fires on the configured prep day
    plan = await ctx.store.get(CurrentPlan)
    if plan is None:
        return
    await messaging.send_md(ctx.bot, ctx.owner_id, "🧑‍🍳 Building the prep-day list…")
    prep = await prep_day.generate(plan, prep_dow)
    await messaging.send_md(ctx.bot, ctx.owner_id, prep_day.format(prep))


@_safe
async def job_ask_feedback() -> None:
    # ask_did_cook self-guards (no plan / no day planned → silent), so we only
    # need the owner to be known and ctx wired.
    ctx.ensure()
    if ctx.owner_id is None:
        return
    await feedback.ask_did_cook()


@_safe
async def job_learn() -> None:
    ctx.ensure()
    if ctx.owner_id is None:
        return
    learned = await learn_mod.run_learn()
    log.info("Scheduled learn complete: %d rules.", len(learned.rules))


# ─── registration ────────────────────────────────────────────────────────────


def schedule_all(scheduler, tz: Optional[str] = None) -> None:
    """Register all cron jobs on ``scheduler`` (an AsyncIOScheduler)."""
    tz = tz or settings.timezone
    wh, wm = _time_env("CRON_WEEKLY_PLAN", "09:00")    # Sunday morning
    dh, dm = _time_env("CRON_DAILY_MENU", "08:00")     # every morning
    ph, pm = _time_env("CRON_PREP_DAY", "08:05")       # daily, self-gates on dow
    fh, fm = _time_env("CRON_FEEDBACK", "20:00")       # every evening
    lh, lm = _time_env("CRON_LEARN", "23:00")          # every night

    scheduler.add_job(
        job_weekly_plan,
        CronTrigger(day_of_week="sun", hour=wh, minute=wm, timezone=tz),
        id="weekly_plan", replace_existing=True,
    )
    scheduler.add_job(
        job_send_today,
        CronTrigger(hour=dh, minute=dm, timezone=tz),
        id="daily_menu", replace_existing=True,
    )
    scheduler.add_job(
        job_send_prep_day,
        CronTrigger(hour=ph, minute=pm, timezone=tz),
        id="prep_day", replace_existing=True,
    )
    scheduler.add_job(
        job_ask_feedback,
        CronTrigger(hour=fh, minute=fm, timezone=tz),
        id="feedback", replace_existing=True,
    )
    scheduler.add_job(
        job_learn,
        CronTrigger(hour=lh, minute=lm, timezone=tz),
        id="learn", replace_existing=True,
    )
    log.info(
        "Scheduled jobs (tz=%s): weekly Sun %02d:%02d, daily %02d:%02d, "
        "prep %02d:%02d, feedback %02d:%02d, learn %02d:%02d.",
        tz, wh, wm, dh, dm, ph, pm, fh, fm, lh, lm,
    )
