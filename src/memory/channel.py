"""Low-level Discord IO for the memory channel.

The memory channel is a **private text channel in a Discord server** that the bot
belongs to (and only the bot + its owner can see). Structured docs are stored as
JSON **file attachments** on a single message per doc, with the message content
("caption") tagged ``#doc:<name> v<n> <date>`` (latest-by-caption wins; previous
versions are deleted on update so the channel stays tidy and the doc window never
drifts out of view). Append-only logs are plain text messages tagged
``#log:<kind> <ts>``.

Because a Discord bot can read channel message history (unlike the Telegram Bot
API, which forced the two-client trick), one client serves both chat UX and
memory IO here.
"""

from __future__ import annotations

import io
import json
import logging
from typing import AsyncIterator, Optional

import discord

from src.config import settings

log = logging.getLogger(__name__)

_DOC_PREFIX = "#doc:"
_LOG_PREFIX = "#log:"

# How far back to scan a channel for docs. Docs are *deleted* when overwritten
# (see post_doc), so the channel only ever holds the latest version of each doc
# plus append-only log messages — but logs accumulate. The previous Telegram
# implementation scanned only 80 newest messages, which silently lost docs once
# >80 logs accumulated. Here we scan generously (configurable via env) and we
# stop early only if every known doc is found before that.
_DEFAULT_DOC_SCAN = int(__import__("os").environ.get("MEMORY_DOC_SCAN", "400") or 400)


async def resolve_channel(bot) -> discord.TextChannel:
    """Resolve the memory text channel from config (channel id or name)."""
    # explicit channel id?
    mc = settings.memory_channel.strip()
    if mc:
        try:
            ch = bot.get_channel(int(mc)) or await bot.fetch_channel(int(mc))
            if isinstance(ch, discord.TextChannel):
                return ch
            log.warning("MEMORY_CHANNEL %r resolved to a non-text channel; "
                        "falling back to name search.", mc)
        except Exception as e:
            log.warning("MEMORY_CHANNEL=%r could not be resolved directly (%s); "
                        "falling back to name search.", mc, e)

    target = settings.memory_channel_name.strip().lower()
    if not target:
        raise RuntimeError(
            "No memory channel configured. Set MEMORY_CHANNEL (channel id) or "
            "MEMORY_CHANNEL_NAME in config/.env."
        )

    # filter by server if given
    guild_id_raw = settings.memory_guild.strip()
    guilds = list(bot.guilds)
    if guild_id_raw:
        try:
            gid = int(guild_id_raw)
            guilds = [g for g in guilds if g.id == gid] or guilds
        except ValueError:
            log.warning("MEMORY_GUILD=%r is not an integer; ignoring.", guild_id_raw)

    for g in guilds:
        for ch in g.text_channels:
            if ch.name.strip().lower() == target:
                return ch

    raise RuntimeError(
        f"Memory text channel named {settings.memory_channel_name!r} not found in "
        f"any server the bot is in. Create one and make sure the bot can see it "
        f"(and has Read Message History)."
    )


def _caption_for(name: str, version: int, date: str) -> str:
    return f"{_DOC_PREFIX}{name} v{version} {date}"


async def post_doc(bot, channel: discord.TextChannel, name: str,
                   data: dict, version: int, date: str) -> int:
    """Upload a JSON file for doc ``name``. Returns the new message id."""
    caption = _caption_for(name, version, date)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(payload)
    buf.name = f"{name}.json"
    msg = await channel.send(content=caption, file=discord.File(buf, filename=f"{name}.json"))
    return msg.id


async def get_doc(bot, channel: discord.TextChannel, name: str,
                  scan: int = _DEFAULT_DOC_SCAN) -> Optional[tuple[dict, int, int]]:
    """Fetch the latest doc ``name``. Returns ``(data, version, msg_id)`` or None.

    Scans the last ``scan`` messages newest-first, short-circuiting once the doc
    is found. ``scan`` defaults to 400 (was 80 on Telegram) because append-only
    logs accumulate here without bound; a doc that hasn't been touched in
    months must still be reachable. Callers also pass ``found_all`` short-circuit.
    """
    target_prefix = f"{_DOC_PREFIX}{name} "
    async for msg in channel.history(limit=scan, oldest_first=False):
        cap = msg.content or ""
        if not cap.startswith(target_prefix):
            continue
        parts = cap.split()
        try:
            version = int(parts[2][1:]) if len(parts) > 2 and parts[2].startswith("v") else 0
        except ValueError:
            version = 0
        # the doc is the first attachment on the message
        if not msg.attachments:
            continue
        att = msg.attachments[0]
        try:
            data_bytes = await att.read()
            data = json.loads(data_bytes.decode("utf-8"))
        except (discord.HTTPException, json.JSONDecodeError) as e:
            log.warning("Doc %s had unreadable JSON (%s); skipping.", name, e)
            continue
        return data, version, msg.id
    return None


async def delete_message(bot, channel: discord.TextChannel, msg_id: int) -> None:
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.delete()
    except discord.NotFound:
        pass  # already gone — fine
    except Exception as e:
        log.warning("Could not delete memory message %s: %s", msg_id, e)


async def append_log(bot, channel: discord.TextChannel, kind: str,
                     entry: dict, ts: str) -> int:
    """Append a tagged log message. Returns its message id."""
    text = f"{_LOG_PREFIX}{kind} {ts}\n{json.dumps(entry, ensure_ascii=False)}"
    msg = await channel.send(text)
    return msg.id


async def iter_logs(bot, channel: discord.TextChannel, kind: str,
                    limit: int = 50) -> AsyncIterator[dict]:
    """Yield log entries of ``kind`` newest-first (up to ``limit``)."""
    prefix = f"{_LOG_PREFIX}{kind} "
    # scan 4× the requested limit, with a floor of 100, to ensure we find enough
    # entries even when docs (which take slots in the channel too) are interleaved.
    scan = max(limit * 4, 100)
    found = 0
    async for msg in channel.history(limit=scan, oldest_first=False):
        text = msg.content or ""
        if not text.startswith(prefix):
            continue
        nl = text.find("\n")
        if nl == -1:
            continue
        try:
            yield json.loads(text[nl + 1 :])
        except json.JSONDecodeError:
            continue
        found += 1
        if found >= limit:
            break
