"""Discord chat package — bot client, message helpers, UI components.

Replaces the old ``src/tg/`` Telegram package. A single discord.py bot client
provides both the chat UX (slash commands + free-text replies over DM) and the
memory channel IO (the bot can read channel history, unlike the Telegram Bot API
which forced two clients).
"""

from src.chat import components, messaging  # noqa: F401
