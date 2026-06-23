"""Shared application context.

A single ``Ctx`` instance holds the long-lived objects (the Discord bot client,
the memory store, the scheduler) so modules can reach them without globals
sprinkled everywhere. ``main.py`` constructs it; everything else reads it.

Importing this module is cheap — it defines only the container. The attributes
are populated at startup, so they are ``Optional`` until then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid runtime import cycles
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from discord.ext.commands import Bot

    from src.memory.store import Store


@dataclass
class Ctx:
    """Wiring container populated by ``main.py`` at startup."""

    bot: Optional["Bot"] = None  # the Discord bot — chat + memory IO
    store: Optional["Store"] = None
    scheduler: Optional["AsyncIOScheduler"] = None
    # Discord id of the owner (you) — the only user allowed to command the bot.
    # Loaded from OWNER_DISCORD_ID (if set) or the `owner` memory doc at startup;
    # claimed and persisted on first DM otherwise.
    owner_id: Optional[int] = None
    # Free-form runtime flags, e.g. {"awaiting_plan_change": True} (keyed by owner).
    flags: dict = field(default_factory=dict)

    def ensure(self) -> "Ctx":
        """Assert the core deps are wired; called by code that needs them."""
        missing = [n for n, v in (("bot", self.bot), ("store", self.store)) if v is None]
        if missing:
            raise RuntimeError(f"Ctx not initialised: missing {missing}.")
        return self


# Module-level singleton. main.py assigns attributes; everyone else imports `ctx`.
ctx = Ctx()
