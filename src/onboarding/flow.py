"""Onboarding orchestration: preliminary → prices → derived → first plan.

State machine driven by ``onboarding_state`` (persisted to the Discord memory
channel so onboarding is resumable across restarts). The bot client routes
component (button) clicks — passing the Discord ``Interaction`` for inline edit /
defer — and free text here while onboarding is active.

Phases:
  preliminary — fixed script (PRELIMINARY[index]); choice/multichoice/text/number
  derived     — first a free-text prices step, then LLM this-or-that until ``done``
  done        — profile/preferences/prices committed; first plan generated & sent
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from src.app import ctx
from src.memory.schema import OnboardingState, Preferences, Prices, Profile
from src.onboarding import derived, preliminary
from src.onboarding.derived import DerivedQuestion
from src.onboarding.preliminary import PRELIMINARY, Question
from src.planner import runner
from src.chat import components, messaging

log = logging.getLogger(__name__)

WELCOME = (
    "👋 **Let's set up your meal-prep assistant.**\n\n"
    "I'll ask a few quick questions — mostly tap-the-button, no typing. "
    "First the essentials, then I'll learn your exact tastes and your local "
    "market prices. You can stop and resume anytime; your answers are saved.\n\n"
    "Tap a button to answer. Let's go 👇"
)


def _is_text_q(q: Question) -> bool:
    return q.kind in ("text", "number")


class OnboardingFlow:
    """All methods are async and assume ``ctx`` is wired."""

    # ── state helpers ────────────────────────────────────────────────────────

    async def _state(self) -> OnboardingState:
        return await ctx.store.get_or_default(OnboardingState)

    async def _save_state(self, st: OnboardingState) -> None:
        from src.util.dt import now_iso
        st.updated = now_iso()
        await ctx.store.set(st)

    async def _profile(self) -> Profile:
        return await ctx.store.get_or_default(Profile)

    async def _prefs(self) -> Preferences:
        return await ctx.store.get_or_default(Preferences)

    async def _prices(self) -> Prices:
        return await ctx.store.get_or_default(Prices)

    # ── entry point ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        ctx.ensure()
        st = await self._state()
        if st.phase == "done":
            await messaging.send_md(
                ctx.bot, ctx.owner_id,
                "You're already set up. Use /plan to (re)generate this week's menu.",
            )
            return
        if not st.started_at:
            from src.util.dt import now_iso
            st.started_at = now_iso()
            await self._save_state(st)
            await messaging.send_md(ctx.bot, ctx.owner_id, WELCOME)
        await self._send_current(st)

    # ─── rendering the current step ──────────────────────────────────────────

    async def _send_current(self, st: OnboardingState) -> None:
        if st.phase == "preliminary":
            await self._send_preliminary(st)
        elif st.phase == "derived":
            if not st.prices_collected:
                await messaging.send_md(ctx.bot, ctx.owner_id, derived.PRICES_PROMPT)
            else:
                await self._send_derived(st)

    async def _send_preliminary(self, st: OnboardingState) -> None:
        idx = st.preliminary_index
        if idx >= len(PRELIMINARY):
            # preliminary complete → move to derived
            st.phase = "derived"
            await self._save_state(st)
            await self._send_current(st)
            return
        q = PRELIMINARY[idx]
        n = f"({idx + 1}/{len(PRELIMINARY)})"
        text = f"**{q.prompt}** {n}"
        if q.help:
            text += f"\n_{q.help}_"
        if q.kind == "choice":
            view = components.onboarding_choice(q.key, q.options)
        elif q.kind == "multichoice":
            sel = set(st.answered.get(f"__mc_{q.key}", []))
            view = components.onboarding_multichoice(q.key, q.options, sel)
        else:  # text / number
            view = None
            text += "\n\n_(type your answer)_"
        await messaging.send_md(ctx.bot, ctx.owner_id, text, view=view)

    async def _send_derived(self, st: OnboardingState) -> None:
        # generate the next derived question if we don't have one pending
        if not st.pending_question:
            q = await self._next_derived(st)
            if q is None or q.done:
                await self._finish(st)
                return
            st.pending_question = q.model_dump()
            await self._save_state(st)
        q = DerivedQuestion.model_validate(st.pending_question)
        text = f"**{q.question}**"
        if q.kind == "choice":
            view = components.onboarding_choice(f"d:{q.key}", q.options)
        elif q.kind == "multichoice":
            sel = set(st.answered.get(f"__mc_d:{q.key}", []))
            view = components.onboarding_multichoice(f"d:{q.key}", q.options, sel)
        else:
            view = None
            text += "\n\n_(type your answer)_"
        await messaging.send_md(ctx.bot, ctx.owner_id, text, view=view)

    async def _next_derived(self, st: OnboardingState) -> Optional[DerivedQuestion]:
        profile = await self._profile()
        prefs = await self._prefs()
        prices = await self._prices()
        answered = list(st.answered.keys())
        try:
            return await derived.next_question(profile, prefs, prices, answered)
        except Exception as e:
            log.error("Derived question generation failed: %s", e)
            await messaging.send_md(
                ctx.bot, ctx.owner_id,
                "⚠ I had trouble thinking up the next question (is the AI model "
                "configured?). I'll wrap up onboarding with what we have. Use /plan "
                "to generate your menu.",
            )
            return None

    # ── callback handling (component button taps) ────────────────────────────
    #
    # Component clicks arrive carrying the Discord Interaction, which we use to
    # ack inline: for multichoice *toggles* we edit the picker message in place
    # with updated marks; for single-choice and "Done" we clear the picker and
    # send the next question as a fresh DM. (Telethon re-sent a new message on
    # every toggle; editing in place is cleaner on Discord.)

    async def handle_callback(self, data: str, interaction: discord.Interaction) -> None:
        ctx.ensure()
        prefix, parts = components.parse_callback(data)
        st = await self._state()
        if st.phase == "done":
            # stale button → just clear the picker politely
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            return
        if st.phase == "preliminary":
            await self._handle_prelim_callback(st, prefix, parts, interaction)
        else:
            await self._handle_derived_callback(st, prefix, parts, interaction)

    async def _handle_prelim_callback(self, st: OnboardingState, prefix: str,
                                     parts: list[str],
                                     interaction: discord.Interaction) -> None:
        idx = st.preliminary_index
        if idx >= len(PRELIMINARY):
            return
        q = PRELIMINARY[idx]
        if prefix == "ob" and len(parts) == 2 and parts[0] == q.key:
            choice = q.options[int(parts[1])]
            profile = await self._profile()
            preliminary.apply_preliminary_answer(profile, q, choice)
            await ctx.store.set(profile)
            # clear the picker, then advance (sends next question)
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            await self._advance_prelim(st)
        elif prefix == "obm" and len(parts) >= 2 and parts[0] == q.key:
            sel_key = f"__mc_{q.key}"
            sel = set(st.answered.get(sel_key, []))
            if parts[1] == "done":
                chosen = [q.options[i] for i in sorted(sel)]
                profile = await self._profile()
                preliminary.apply_preliminary_answer(profile, q, chosen)
                st.answered.pop(sel_key, None)
                await ctx.store.set(profile)
                try:
                    await interaction.response.edit_message(view=None)
                except discord.HTTPException:
                    pass
                await self._advance_prelim(st)
            else:
                i = int(parts[1])
                sel ^= {i}
                st.answered[sel_key] = sorted(sel)
                await self._save_state(st)
                # edit the picker in place with updated ticks
                new_view = components.onboarding_multichoice(q.key, q.options, sel)
                try:
                    await interaction.response.edit_message(view=new_view)
                except discord.HTTPException:
                    # response already done or msg gone; send a fresh message
                    await messaging.send_md(
                        ctx.bot, ctx.owner_id,
                        f"**{q.prompt}** ({idx + 1}/{len(PRELIMINARY)})",
                        view=new_view,
                    )

    async def _advance_prelim(self, st: OnboardingState) -> None:
        st.preliminary_index += 1
        await self._save_state(st)
        await self._send_current(st)

    async def _handle_derived_callback(self, st: OnboardingState, prefix: str,
                                       parts: list[str],
                                       interaction: discord.Interaction) -> None:
        if not st.pending_question:
            return
        q = DerivedQuestion.model_validate(st.pending_question)
        key = f"d:{q.key}"
        if prefix == "ob" and len(parts) == 2 and parts[0] == key:
            choice = q.options[int(parts[1])]
            prefs = await self._prefs()
            derived.apply_derived_answer(prefs, q, choice)
            await ctx.store.set(prefs)
            st.answered[q.key] = choice
            st.pending_question = None
            await self._save_state(st)
            try:
                await interaction.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            await self._send_current(st)
        elif prefix == "obm" and len(parts) >= 2 and parts[0] == key:
            sel_key = f"__mc_{key}"
            sel = set(st.answered.get(sel_key, []))
            if parts[1] == "done":
                chosen = [q.options[i] for i in sorted(sel)]
                prefs = await self._prefs()
                derived.apply_derived_answer(prefs, q, chosen)
                st.answered[q.key] = chosen
                st.answered.pop(sel_key, None)
                await ctx.store.set(prefs)
                st.pending_question = None
                await self._save_state(st)
                try:
                    await interaction.response.edit_message(view=None)
                except discord.HTTPException:
                    pass
                await self._send_current(st)
            else:
                i = int(parts[1])
                sel ^= {i}
                st.answered[sel_key] = sorted(sel)
                await self._save_state(st)
                new_view = components.onboarding_multichoice(key, q.options, sel)
                try:
                    await interaction.response.edit_message(view=new_view)
                except discord.HTTPException:
                    await messaging.send_md(
                        ctx.bot, ctx.owner_id, f"**{q.question}**", view=new_view)

    # ── free-text handling (text / number / prices answers) ──────────────────

    async def handle_text(self, text: str) -> bool:
        """Apply a free-text answer if we're expecting one. Returns True if consumed."""
        ctx.ensure()
        st = await self._state()
        if st.phase == "done":
            return False
        if st.phase == "preliminary":
            idx = st.preliminary_index
            if idx >= len(PRELIMINARY):
                return False
            q = PRELIMINARY[idx]
            if not _is_text_q(q):
                return False
            profile = await self._profile()
            preliminary.apply_preliminary_answer(profile, q, text)
            await ctx.store.set(profile)
            await self._advance_prelim(st)
            return True
        # derived phase
        if not st.prices_collected:
            prices = await self._prices()
            profile = await self._profile()
            prices = derived.apply_prices_answer(prices, text, profile.currency)
            await ctx.store.set(prices)
            st.prices_collected = True
            await self._save_state(st)
            from src.util.prices import format_prices
            await messaging.send_md(
                ctx.bot, ctx.owner_id,
                f"Saved your market prices:\n{format_prices(prices)}\n\n"
                "Now a few quick preference questions…",
            )
            await self._send_current(st)
            return True
        # derived text question
        if st.pending_question:
            q = DerivedQuestion.model_validate(st.pending_question)
            if q.kind == "text":
                prefs = await self._prefs()
                derived.apply_derived_answer(prefs, q, text.strip())
                await ctx.store.set(prefs)
                st.answered[q.key] = text.strip()
                st.pending_question = None
                await self._save_state(st)
                await self._send_current(st)
                return True
        return False

    # ── finish ───────────────────────────────────────────────────────────────

    async def _finish(self, st: OnboardingState) -> None:
        st.phase = "done"
        st.pending_question = None
        await self._save_state(st)
        await messaging.send_md(
            ctx.bot, ctx.owner_id,
            "✅ **Onboarding complete!** I've saved your profile, preferences, and "
            "market prices. Generating your first weekly menu now…",
        )
        try:
            plan = await runner.generate_and_store_plan()
            await runner.send_plan_for_approval(plan)
        except Exception as e:
            log.error("First plan generation failed: %s", e)
            await messaging.send_md(
                ctx.bot, ctx.owner_id,
                "⚠ Couldn't generate the first plan (is the AI model configured?). "
                "Use /plan to try again once it's set up.",
            )

    # ── query used by the bot to decide routing ─────────────────────────────

    async def is_active(self) -> bool:
        st = await self._state()
        return st.phase != "done"

    async def expecting_text(self) -> bool:
        st = await self._state()
        if st.phase == "done":
            return False
        if st.phase == "preliminary":
            idx = st.preliminary_index
            if idx < len(PRELIMINARY):
                return _is_text_q(PRELIMINARY[idx])
            return False
        # derived
        if not st.prices_collected:
            return True
        if st.pending_question:
            q = DerivedQuestion.model_validate(st.pending_question)
            return q.kind == "text"
        return False


# Module-level singleton used by the bot client.
flow = OnboardingFlow()
