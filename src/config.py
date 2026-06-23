"""Typed configuration loaded from config/.env.

All settings are read once at import time and exposed as the module-level
``Settings`` instance ``settings``. Missing *required* values raise a clear
``ConfigError`` with instructions, instead of a cryptic failure deeper in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Project root = parent of this file's package (src/ -> mealprep-bot/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / "config" / ".env"

# Load .env if present (no error when absent — env vars may be set directly).
load_dotenv(ENV_PATH)


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing or invalid."""


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(
            f"Missing required env var {name!r}. "
            f"Copy config/.env.example to config/.env and fill it in."
        )
    return val


def _opt(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _opt_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"Env var {name!r} must be an integer, got {raw!r}.")


def _opt_int_req(name: str, default: int) -> int:
    v = _opt_int(name, default)
    return v if v is not None else default


@dataclass(frozen=True)
class Settings:
    # ── Discord core ────────────────────────────────────────────────────────
    bot_token: str

    # Owner lock. If set, ONLY this Discord user id may command the bot
    # (persistent across restarts — recommended). If unset, the first user to
    # DM the bot becomes the owner and is persisted to a memory doc.
    owner_id: Optional[int]

    # ── Memory channel (a private text channel in a Discord server) ───────────
    # Identify by channel id: set MEMORY_CHANNEL to the channel's numeric id.
    # OR leave MEMORY_CHANNEL blank and set MEMORY_CHANNEL_NAME to auto-detect.
    # MEMORY_GUILD optionally narrows the search by server id (otherwise every
    # guild the bot is in is scanned).
    memory_guild: str
    memory_channel: str
    memory_channel_name: str

    # ── Scheduling ───────────────────────────────────────────────────────────
    timezone: str

    # ── LLM (OpenAI-compatible) ──────────────────────────────────────────────
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: int

    # ── Misc ─────────────────────────────────────────────────────────────────
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    @property
    def memory_channel_resolved(self) -> bool:
        return bool(self.memory_channel)


def _load() -> Settings:
    # Required Discord bot token — validated eagerly so a missing token surfaces
    # at startup rather than as a discord.py connect failure later.
    bot_token = _req("DISCORD_BOT_TOKEN")

    return Settings(
        bot_token=bot_token,
        owner_id=_opt_int("OWNER_DISCORD_ID"),
        memory_guild=_opt("MEMORY_GUILD"),
        memory_channel=_opt("MEMORY_CHANNEL"),
        memory_channel_name=_opt("MEMORY_CHANNEL_NAME", "MealPrepMemory"),
        timezone=_opt("TIMEZONE", "Asia/Kolkata"),
        llm_base_url=_opt("LLM_BASE_URL"),
        llm_api_key=_opt("LLM_API_KEY"),
        llm_model=_opt("LLM_MODEL", "minimax-m3-free"),
        llm_timeout=_opt_int_req("LLM_TIMEOUT", 120),
    )


settings = _load()
