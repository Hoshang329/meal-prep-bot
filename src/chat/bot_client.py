"""The Discord bot client — the friendly chat interface you talk to.

Registers slash commands, routes component (button) taps to a single router
delegated to flow/feedback/approval, and handles free-text owner messages for
price corrections, plan-change follow-ups and onboarding text answers.

Everything the user can do is here; the heavy lifting lives in the planner /
onboarding / feedback modules and the memory store. All bot → user output goes
to the owner's DM via :mod:`src.chat.messaging` so the user has one tidy place.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.app import ctx
from src.feedback import learn as learn_mod
from src.feedback import loop as feedback
from src.memory.schema import (
    CurrentPlan, OwnerDoc, Pantry, PlanHistoryEntry, Prices, Profile,
)
from src.onboarding.flow import flow
from src.planner import daily, grocery, prep_day, runner
from src.recipes import library as recipes
from src.chat import components, messaging
from src.util import prices as prices_util
from src.util.dt import now_iso, today_iso

log = logging.getLogger(__name__)

# In-memory "awaiting" flags for free-text follow-ups (keyed by owner).
_FLAG_PLAN_CHANGE = "awaiting_plan_change"
_FLAG_GROC_CHANGE = "awaiting_grocery_change"

HELP = (
    "**Meal-Prep Bot** — commands:\n"
    "/start — begin or resume onboarding\n"
    "/plan — (re)generate this week's menu for approval\n"
    "/today — today's menu + instructions\n"
    "/prep — batch-prep task list for prep day\n"
    "/feedback — \"did you cook today?\" ping\n"
    "/price `<item> <price/unit>` — correct a market price (e.g. `/price rice 90/kg`)\n"
    "/learn — re-distil your feedback into learnings now\n"
    "/recipes — show your recipe library\n"
    "/show memory — dump current memory\n"
    "/show `<doc>` — dump one memory doc (e.g. `/show profile`)\n"
    "/init — re-resolve the memory channel\n"
    "/cancel — cancel a pending change/request\n\n"
    "Or just type: *rice is now ₹90/kg*, *I hated Tuesday's dinner*, etc."
)


# ─── client construction ─────────────────────────────────────────────────────


def build_bot_client() -> commands.Bot:
    intents = discord.Intents.default()
    # Free-text reading from the owner: required for guild-channel messages;
    # DMs to the bot are readable without it, but enabling it means the user
    # can chat with the bot in a server channel too.
    intents.message_content = True
    return commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ─── owner guard ─────────────────────────────────────────────────────────────


async def _ensure_owner(user_id: int) -> bool:
    """Lock the bot to a single user. Persisted to the ``owner`` memory doc."""
    if ctx.owner_id is None:
        ctx.owner_id = user_id
        try:
            await ctx.store.set(OwnerDoc(discord_id=user_id, claimed_at=now_iso()))
        except Exception as e:  # never let a store hiccup block the lock
            log.warning("Couldn't persist owner doc: %s", e)
        log.info("Owner claimed on first message: id %s.", user_id)
        # nudged below by the caller when reporting success
    return user_id == ctx.owner_id


def _flag(name: str) -> str:
    return f"{name}:{ctx.owner_id}"


def _clear_reason_flags() -> None:
    """Pop the ephemeral feedback-reason flags for the current owner."""
    ctx.flags.pop(f"fb_reason:{ctx.owner_id}", None)
    ctx.flags.pop(f"fb_reason:{ctx.owner_id}:outcome", None)


# ─── handler registration ────────────────────────────────────────────────────


def register_handlers(bot: commands.Bot) -> None:
    # Install the component-click router everything delegates to.
    components.set_router(_on_component_click)

    # ── free text in DMs / channels (owner only) ─────────────────────────
    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        # owner lock / authorisation
        if not await _ensure_owner(message.author.id):
            return
        # slash commands don't arrive here; ignore the bot's own command prefix
        content = message.content or ""
        if not content:
            return
        await _handle_text(content)

    # ─── slash commands ───────────────────────────────────────────────────
    _register_slash(bot)


def _register_slash(bot: commands.Bot) -> None:

    @bot.tree.command(name="start", description="Begin or resume onboarding")
    async def _cmd_start(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Starting…", ephemeral=True)
        await _dispatch_command("start", "")

    @bot.tree.command(name="help", description="List commands")
    async def _cmd_help(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message(HELP, ephemeral=True)

    @bot.tree.command(name="plan", description="(Re)generate this week's menu for approval")
    async def _cmd_plan(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Planning…", ephemeral=True)
        await _dispatch_command("plan", "")

    @bot.tree.command(name="today", description="Show today's menu + instructions")
    async def _cmd_today(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Sent in DM 📩", ephemeral=True)
        await _dispatch_command("today", "")

    @bot.tree.command(name="prep", description="Batch-prep task list for prep day")
    async def _cmd_prep(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Building prep list…", ephemeral=True)
        await _dispatch_command("prep", "")

    @bot.tree.command(name="feedback", description="Ask 'did you cook today?'")
    async def _cmd_feedback(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Asking…", ephemeral=True)
        await _dispatch_command("feedback", "")

    @bot.tree.command(name="price", description="Correct a market price, e.g. /price rice 90/kg")
    @app_commands.describe(spec="<item> <price/unit>  e.g. 'rice 90/kg'")
    async def _cmd_price(interaction: discord.Interaction, spec: str):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Updating…", ephemeral=True)
        await _dispatch_command("price", spec)

    @bot.tree.command(name="learn", description="Re-distil feedback into learnings now")
    async def _cmd_learn(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Learning…", ephemeral=True)
        await _dispatch_command("learn", "")

    @bot.tree.command(name="recipes", description="Show your recipe library")
    async def _cmd_recipes(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Sent in DM 📩", ephemeral=True)
        await _dispatch_command("recipes", "")

    @bot.tree.command(name="show",
                      description="Dump current memory (or one doc, e.g. /show profile)")
    @app_commands.describe(doc="doc name or 'memory' for the full snapshot")
    async def _cmd_show(interaction: discord.Interaction, doc: Optional[str] = None):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Sent in DM 📩", ephemeral=True)
        await _dispatch_command("show", doc or "")

    @bot.tree.command(name="init", description="Re-resolve the memory channel")
    async def _cmd_init(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await interaction.response.send_message("Re-resolving…", ephemeral=True)
        await _dispatch_command("init", "")

    @bot.tree.command(name="cancel", description="Cancel a pending plan/grocery change or feedback reason")
    async def _cmd_cancel(interaction: discord.Interaction):
        if not await _ensure_owner(interaction.user.id):
            await interaction.response.send_message("Not for you.", ephemeral=True)
            return
        await _dispatch_command("cancel", "")
        await interaction.response.send_message("Cancelled ✋", ephemeral=True)


# ─── command dispatch ──────────────────────────────────────────────────────


async def _dispatch_command(cmd: str, args: str) -> None:
    bot, cid = ctx.bot, ctx.owner_id
    try:
        if cmd == "start":
            if await flow.is_active():
                await flow.start()
            else:
                await messaging.send_md(
                    bot, cid,
                    "👋 You're set up. Use /plan to (re)generate this week's menu, "
                    "/today for today's plan, /help for all commands.",
                )
        elif cmd == "help":
            await messaging.send_md(bot, cid, HELP)
        elif cmd == "plan":
            await _cmd_plan_run()
        elif cmd == "today":
            await _cmd_today_run()
        elif cmd == "prep":
            await _cmd_prep_run()
        elif cmd == "feedback":
            await feedback.ask_did_cook()
        elif cmd == "price":
            await _cmd_price_run(args)
        elif cmd == "learn":
            learned = await learn_mod.run_learn()
            await messaging.send_md(bot, cid, learn_mod.format(learned))
        elif cmd == "recipes":
            lib = await recipes.list_all()
            await messaging.send_md(bot, cid, recipes.format(lib))
        elif cmd == "show":
            await _cmd_show_run(args)
        elif cmd == "init":
            await ctx.store.setup()
            await messaging.send_md(bot, cid, "♻ Memory channel re-resolved and cache primed.")
        elif cmd == "cancel":
            ctx.flags.pop(_flag(_FLAG_PLAN_CHANGE), None)
            ctx.flags.pop(_flag(_FLAG_GROC_CHANGE), None)
            _clear_reason_flags()
        else:
            await messaging.send_md(bot, cid, f"Unknown command /{cmd}. Try /help.")
    except Exception as e:
        log.exception("Command /%s failed: %s", cmd, e)
        await messaging.send_md(bot, cid, f"⚠ Something went wrong running /{cmd}: {e}")


async def _cmd_plan_run() -> None:
    await messaging.send_md(ctx.bot, ctx.owner_id, "🧠 Planning your week…")
    plan = await runner.generate_and_store_plan()
    await runner.send_plan_for_approval(plan)


async def _cmd_today_run() -> None:
    plan = await ctx.store.get(CurrentPlan)
    if plan is None:
        await messaging.send_md(ctx.bot, ctx.owner_id, "No plan yet. Use /plan first.")
        return
    profile = await ctx.store.get_or_default(Profile)
    await messaging.send_md(ctx.bot, ctx.owner_id, daily.format_today(plan, profile))


async def _cmd_prep_run() -> None:
    plan = await ctx.store.get(CurrentPlan)
    if plan is None:
        await messaging.send_md(ctx.bot, ctx.owner_id, "No plan yet. Use /plan first.")
        return
    profile = await ctx.store.get_or_default(Profile)
    prep_day_dow = profile.prep_day or "Sunday"
    await messaging.send_md(ctx.bot, ctx.owner_id, "🧑‍🍳 Building the prep-day list…")
    prep = await prep_day.generate(plan, prep_day_dow)
    await messaging.send_md(ctx.bot, ctx.owner_id, prep_day.format(prep))


async def _cmd_price_run(args: str) -> None:
    if not args:
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            "Usage: /price <item> <price/unit>\nExample: /price rice 90/kg")
        return
    profile = await ctx.store.get_or_default(Profile)
    prices = await ctx.store.get_or_default(Prices)
    parsed = prices_util.parse_price_block(args, currency=profile.currency)
    if not parsed:
        await messaging.send_md(ctx.bot, ctx.owner_id,
                                "Couldn't parse that. Try: /price rice 90/kg")
        return
    prices = prices_util.merge_prices(prices, parsed)
    await ctx.store.set(prices)
    await messaging.send_md(ctx.bot, ctx.owner_id,
                            f"Updated prices:\n{prices_util.format_prices(prices)}")


async def _cmd_show_run(args: str) -> None:
    import json
    snap = ctx.store.cache_snapshot()
    if args.lower() == "memory" or not args:
        lines = ["🗄 **Memory (from Discord memory channel, v = version):**"]
        for name, data in sorted(snap.items()):
            if name == "owner":
                continue  # don't advertise the owner doc in /show memory
            v = ctx.store.cached_version(_model_for(name))
            lines.append(f"• {name} (v{v}): {_short_repr(data)}")
        text = "\n".join(lines)
        if len(text) > 3800:
            buf = json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8")
            await messaging.send_file(ctx.bot, ctx.owner_id, buf,
                                      "memory_dump.json", caption="memory_dump.json")
            await messaging.send_md(ctx.bot, ctx.owner_id,
                                    "Memory dump sent as a file above 📎")
        else:
            await messaging.send_md(ctx.bot, ctx.owner_id, text)
    else:
        doc = snap.get(args.lower())
        if doc is None:
            await messaging.send_md(ctx.bot, ctx.owner_id, f"No doc named {args!r}.")
            return
        text = f"```\n{json.dumps(doc, ensure_ascii=False, indent=2)[:3800]}\n```"
        await messaging.send_md(ctx.bot, ctx.owner_id, text)


def _model_for(name: str):
    from src.memory.schema import DOC_MODELS
    return DOC_MODELS.get(name, type("X", (), {}))


def _short_repr(data) -> str:
    if isinstance(data, dict):
        return f"{len(data)} keys"
    if isinstance(data, list):
        return f"{len(data)} items"
    return str(data)[:40]


# ─── free-text handling ──────────────────────────────────────────────────────


async def _handle_text(text: str) -> None:
    # 1) pending plan change follow-up
    if ctx.flags.get(_flag(_FLAG_PLAN_CHANGE)):
        ctx.flags.pop(_flag(_FLAG_PLAN_CHANGE), None)
        await messaging.send_md(ctx.bot, ctx.owner_id, "🧑‍🍳 Revising the plan…")
        try:
            plan = await runner.generate_and_store_plan(changes=text)
            await runner.send_plan_for_approval(plan)
        except Exception as e:
            log.exception("Plan revision failed: %s", e)
            await messaging.send_md(ctx.bot, ctx.owner_id, f"⚠ Revision failed: {e}")
        return

    # 2) pending grocery change follow-up
    if ctx.flags.get(_flag(_FLAG_GROC_CHANGE)):
        ctx.flags.pop(_flag(_FLAG_GROC_CHANGE), None)
        plan = await ctx.store.get(CurrentPlan)
        if plan is None:
            await messaging.send_md(ctx.bot, ctx.owner_id, "No current plan to edit.")
            return
        prices = await ctx.store.get_or_default(Prices)
        pantry = await ctx.store.get_or_default(Pantry)
        await messaging.send_md(ctx.bot, ctx.owner_id, "🛒 Updating the list…")
        plan = await grocery.revise(plan, prices, pantry, text)
        await ctx.store.set(plan)
        await runner.send_grocery_list(plan)
        return

    # 3) feedback reason follow-up (only if owner is in the middle of one)
    if await feedback.handle_reason_text(text):
        return

    # 4) onboarding free-text answers
    if await flow.is_active() and await flow.expecting_text():
        await flow.handle_text(text)
        return

    # 5) generic free text: try a price correction, else log as an opinion note
    profile = await ctx.store.get_or_default(Profile)
    parsed = prices_util.parse_price_block(text, currency=profile.currency)
    if parsed:
        prices = await ctx.store.get_or_default(Prices)
        prices = prices_util.merge_prices(prices, parsed)
        await ctx.store.set(prices)
        await messaging.send_md(ctx.bot, ctx.owner_id,
                                f"Updated prices:\n{prices_util.format_prices(prices)}")
        return

    from src.memory.schema import FeedbackEntry
    await ctx.store.append_log("feedback",
                               FeedbackEntry(date=today_iso(), outcome="other",
                                             reason=text.strip(), logged_at=now_iso()))
    await messaging.send_md(
        ctx.bot, ctx.owner_id,
        "Noted 🙏 — I'll factor that into future plans. "
        "(Use /feedback to log whether you cooked today, or /price to fix a price.)",
    )


# ─── component (button) dispatch ─────────────────────────────────────────────


async def _on_component_click(custom_id: str, interaction: discord.Interaction) -> None:
    if not await _ensure_owner(interaction.user.id):
        try:
            await interaction.response.send_message("Not for you.", ephemeral=True)
        except discord.HTTPException:
            pass
        return

    prefix, parts = components.parse_callback(custom_id)
    try:
        if prefix in ("ob", "obm"):
            # flow handles its own interaction ack (edit_message for toggles,
            # edit+clear plus new DM for single-choice / done).
            await flow.handle_callback(custom_id, interaction)
        elif prefix == "app":
            await interaction.response.edit_message(view=None)  # clear the picker
            await _cb_approval(parts)
        elif prefix == "groc":
            await interaction.response.edit_message(view=None)
            await _cb_grocery(parts)
        elif prefix == "fb":
            await interaction.response.edit_message(view=None)
            await feedback.handle_callback(custom_id, interaction)
        elif prefix == "yn":
            await interaction.response.edit_message(view=None)
        else:
            await interaction.response.send_message("Unknown button.", ephemeral=True)
    except Exception as e:
        log.exception("Component %s failed: %s", custom_id, e)
        if interaction.response.is_done():
            try:
                await interaction.followup.send(f"Error: {e}", ephemeral=True)
            except discord.HTTPException:
                pass
        else:
            try:
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            except discord.HTTPException:
                pass


async def _cb_approval(parts: list[str]) -> None:
    if not parts:
        return
    action = parts[0]
    plan = await ctx.store.get(CurrentPlan)
    if plan is None:
        await messaging.send_md(ctx.bot, ctx.owner_id,
                                "No plan to approve. Use /plan first.")
        return
    if action == "approve":
        plan.status = "approved"
        plan.approved_at = now_iso()
        await ctx.store.set(plan)
        await recipes.ingest_plan(plan)
        await ctx.store.append_log("plan_history", PlanHistoryEntry(
            week_of=plan.week_of, status="approved",
            cost=plan.est_cost, logged_at=now_iso(),
        ))
        await messaging.send_md(ctx.bot, ctx.owner_id,
                                "✅ Plan approved & saved. Here's your grocery list:")
        await runner.send_grocery_list(plan)
    elif action == "change":
        ctx.flags[_flag(_FLAG_PLAN_CHANGE)] = True
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            "What would you like to change? (e.g. \"swap Tuesday dinner for something "
            "lighter\", \"no fish this week\", \"more paneer\"). Type it below, or /cancel.",
        )


async def _cb_grocery(parts: list[str]) -> None:
    if not parts:
        return
    action = parts[0]
    if action == "ok":
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            "✅ Grocery list confirmed. I'll send your daily menu each morning and the "
            "prep-day task list on your prep day. You'll get a \"did you cook?\" ping "
            "each evening so I can learn.",
        )
    elif action == "change":
        ctx.flags[_flag(_FLAG_GROC_CHANGE)] = True
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            "What would you like to change? (e.g. \"add 2 eggs\", \"remove chicken\", "
            "\"I already have rice\"). Type it below, or /cancel.",
        )
