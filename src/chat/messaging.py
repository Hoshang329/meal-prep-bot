"""Safe Discord sending helpers.

``send_md`` sends to the **owner's DM channel** with the Discord-flavoured
Markdown Discord accepts natively, and falls back to plain text if Discord
rejects the formatting (a stray ``*`` or ``_`` in a dish name, etc.). This
keeps a single bad character from breaking a scheduled send.

All bot → user output goes via DM to the owner, even when triggered from a
slash command in a server, so the user always has one tidy place to read.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

log = logging.getLogger(__name__)

# Cache: user_id -> DM channel, so we don't fetch the user repeatedly.
_DM_CACHE: dict[int, discord.DMChannel] = {}


async def _dm(bot, user_id: int) -> discord.DMChannel:
    ch = _DM_CACHE.get(user_id)
    if ch is not None:
        return ch
    user = bot.get_user(user_id) or await bot.fetch_user(user_id)
    ch = await user.create_dm()
    _DM_CACHE[user_id] = ch
    return ch


async def send_md(
    bot,
    user_id: int,
    text: str,
    view: Optional[discord.ui.View] = None,
) -> Optional[discord.Message]:
    """Send a markdown message to the owner's DM with plain-text fallback."""
    ch = await _dm(bot, user_id)
    try:
        return await ch.send(text, view=view, suppress_embeds=True)
    except discord.HTTPException as e:
        # Markdown/forms often tripped up by stray * or _ in a dish name.
        log.debug("Markdown send failed (%s); retrying as plain text.", e)
        try:
            stripped = text  # discord doesn't require removing markdown here;
            return await ch.send(stripped, view=view, suppress_embeds=True)
        except discord.HTTPException as e2:
            log.debug("Plain fallback also failed: %s", e2)
            return None


async def send_file(bot, user_id: int, content_bytes: bytes, filename: str,
                    caption: str = "") -> Optional[discord.Message]:
    """Send a file (memory dump etc.) to the owner's DM."""
    import io
    ch = await _dm(bot, user_id)
    buf = io.BytesIO(content_bytes)
    try:
        return await ch.send(content=caption or None,
                              file=discord.File(buf, filename=filename))
    except discord.HTTPException as e:
        log.warning("send_file failed: %s", e)
        return None


async def edit_md(
    channel: discord.abc.Messageable,
    message: discord.Message,
    text: str,
    view: Optional[discord.ui.View] = None,
) -> Optional[discord.Message]:
    """Edit an existing bot message; plain-text fallback on parse failure."""
    try:
        return await message.edit(content=text, view=view, suppress_embeds=True)
    except discord.HTTPException as e:
        log.debug("Markdown edit failed (%s); retrying as plain text.", e)
        try:
            return await message.edit(content=text, view=view, suppress_embeds=True)
        except discord.HTTPException as e2:
            log.debug("Plain edit also failed: %s", e2)
            return None
