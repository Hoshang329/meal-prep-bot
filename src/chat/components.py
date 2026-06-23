"""Discord UI components (buttons) replacing the old Telegram inline keyboards.

callback custom_id strings carry the same prefix scheme as before:
  ob:<key>:<idx>       onboarding single-choice
  obm:<key>:<idx>      onboarding multi-choice toggle
  obm:<key>:done       onboarding multi-choice confirm
  app:approve|change   plan approval
  groc:ok|change       grocery confirmation
  fb:cook|other|skip:<date>   "did you cook?" feedback
  yn:<topic>:y|n       generic yes/no

Discord custom_id strings have a 100-byte limit, which all these stay well under.

Routing: every button shares a single ``_RoutedButton`` class whose callback
delegates to a module-level ``_ROUTER`` callable installed once at startup by
``chat.bot_client``. That keeps the routing knowledge in one place instead of
wiring per-button closures everywhere.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

_ROUTER: Optional[Callable[[str, discord.Interaction], Awaitable[None]]] = None


def set_router(fn) -> None:
    """Install the component-click router. Called once from bot_client at startup."""
    global _ROUTER
    _ROUTER = fn


class _RoutedButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction) -> None:
        if _ROUTER is None:
            await interaction.response.send_message("Bot still starting up…",
                                                     ephemeral=True)
            return
        cid = self.custom_id or (interaction.data or {}).get("custom_id", "")
        await _ROUTER(cid, interaction)


def parse_callback(custom_id: str) -> tuple[str, list[str]]:
    """Split ``custom_id`` into ``(prefix, parts)``."""
    parts = custom_id.split(":")
    return parts[0], parts[1:]


def _new_view() -> discord.ui.View:
    # timeout=None so components stay valid until they're explicitly cleared
    # (component edits are how we dismiss pickers after use).
    return discord.ui.View(timeout=None)


def _button(label: str, custom_id: str,
            style: discord.ButtonStyle = discord.ButtonStyle.primary,
            row: Optional[int] = None) -> _RoutedButton:
    return _RoutedButton(label=label, custom_id=custom_id, style=style, row=row)


def _rows_for(n: int, per_row: int = 2) -> list[int]:
    return [i // max(per_row, 1) for i in range(n)]


# ─── Onboarding ──────────────────────────────────────────────────────────────


def onboarding_choice(key: str, options: list[str], per_row: int = 2) -> discord.ui.View:
    v = _new_view()
    for i, o in enumerate(options):
        v.add_item(_button(o, f"ob:{key}:{i}", row=i // max(per_row, 1)))
    return v


def onboarding_multichoice(key: str, options: list[str], selected: set[int],
                           per_row: int = 2) -> discord.ui.View:
    v = _new_view()
    for i, o in enumerate(options):
        mark = "✅ " if i in selected else "○ "
        v.add_item(_button(mark + o, f"obm:{key}:{i}",
                           row=i // max(per_row, 1),
                           style=discord.ButtonStyle.secondary))
    # Done button on its own row
    n = len(options)
    v.add_item(_button("Done ✅", f"obm:{key}:done",
                       row=(n // max(per_row, 1)) + 1,
                       style=discord.ButtonStyle.success))
    return v


# ─── Plan / grocery approval ─────────────────────────────────────────────────


def approve_change() -> discord.ui.View:
    v = _new_view()
    v.add_item(_button("Approve ✅", "app:approve",
                       style=discord.ButtonStyle.success))
    v.add_item(_button("Request changes ✏️", "app:change",
                       style=discord.ButtonStyle.secondary))
    return v


def grocery_confirm() -> discord.ui.View:
    v = _new_view()
    v.add_item(_button("Got it ✅", "groc:ok",
                       style=discord.ButtonStyle.success))
    v.add_item(_button("Need changes", "groc:change",
                       style=discord.ButtonStyle.secondary))
    return v


# ─── Feedback ────────────────────────────────────────────────────────────────


def feedback_did_cook(date_iso: str) -> discord.ui.View:
    v = _new_view()
    v.add_item(_button("Yes 👍", f"fb:cook:{date_iso}",
                       style=discord.ButtonStyle.success))
    v.add_item(_button("Made something else", f"fb:other:{date_iso}",
                       style=discord.ButtonStyle.secondary))
    v.add_item(_button("Skipped", f"fb:skip:{date_iso}",
                       style=discord.ButtonStyle.danger))
    return v


def yes_no(topic: str) -> discord.ui.View:
    v = _new_view()
    v.add_item(_button("Yes", f"yn:{topic}:y", style=discord.ButtonStyle.success))
    v.add_item(_button("No", f"yn:{topic}:n", style=discord.ButtonStyle.secondary))
    return v
