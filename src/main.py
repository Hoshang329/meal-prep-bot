"""Entry point: build the Discord bot, wire ``ctx``, prime memory, register
handlers, start the scheduler, then keep the process alive on the bot client.

Unlike the Telegram design (which needed two clients — a BotFather bot for chat
UX plus your own MTProto account to read the memory channel), a single Discord
bot can both chat AND read channel message history. So there's only one client
here, and the private memory channel is just a private text channel in a server
the bot also belongs to.

Startup order:
  1. Build the discord.py bot and assign it to ``ctx``.
  2. Resolve the memory channel and prime the in-process cache (``store.setup``)
     before handlers fire.
  3. Recover the owner lock (env override has priority; otherwise the persisted
     ``owner`` doc). When undefined, owner will be claimed on first DM instead.
  4. Register slash commands + component/router handlers.
  5. Start the scheduler, then run the bot (blocks until logout).

Run with ``python -m src.main`` (from the project root).
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.app import ctx
from src.config import ConfigError, settings
from src.memory.schema import OwnerDoc
from src.memory.store import Store
from src.scheduler import jobs
from src.chat.bot_client import build_bot_client, register_handlers

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def main() -> None:
    _setup_logging()
    log.info("Starting meal-prep bot (timezone=%s, model=%s).",
             settings.timezone, settings.llm_model or "(none)")

    # 1. build + wire the single Discord bot client
    bot = build_bot_client()
    ctx.bot = bot
    ctx.store = Store(bot)

    # 2. resolve the memory channel & prime the cache (needs the bot connected
    #    before we can fetch guilds/channels — connect first, prime after ready).
    @bot.event
    async def on_ready():
        log.info("Discord bot ready as %s (id %s).", bot.user, bot.user.id)

        # resolve + prime memory once the bot can see its guilds
        await ctx.store.setup()

        # owner lock (env override > persisted doc > claim-on-first-DM)
        if settings.owner_id is not None:
            ctx.owner_id = settings.owner_id
            log.info("Owner lock from env: id %s.", ctx.owner_id)
        else:
            owner_doc = await ctx.store.get(OwnerDoc)
            if owner_doc and owner_doc.discord_id is not None:
                ctx.owner_id = owner_doc.discord_id
                log.info("Owner lock from memory doc: id %s.", ctx.owner_id)
            else:
                log.info("No owner lock configured — will claim on first DM "
                        "(add OWNER_DISCORD_ID to config/.env to lock permanently).")

        # sync slash commands globally (may take a few minutes to propagate on first run)
        # small caveat: rollback after register_handlers call below.
        register_handlers(bot)  # synchronously installs slash + event handlers + router
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash commands.", len(synced))
        except Exception as e:
            log.error("Slash command sync failed: %s", e)

        # 5. start the scheduler (kept phone-friendly: coalesce collapses
        # missed fires into one, max_instances=1 stops overlapping runs, and a
        # 1h misfire grace means a device that was off gets its missed ping).
        scheduler = AsyncIOScheduler(
            timezone=settings.timezone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 3600,
            },
        )
        jobs.schedule_all(scheduler)
        scheduler.start()
        ctx.scheduler = scheduler
        log.info("Scheduler started. Bot is live — talk to it on Discord.")

    # block until the bot disconnects / Ctrl-C
    await bot.start(settings.bot_token)


def run() -> None:
    try:
        asyncio.run(main())
    except ConfigError as e:
        print(f"⚠ Config error: {e}")
    except KeyboardInterrupt:
        print("\nbye 👋")


if __name__ == "__main__":
    run()
