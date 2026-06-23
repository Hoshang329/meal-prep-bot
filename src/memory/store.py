"""High-level typed memory store over the Discord memory channel.

Wraps :mod:`src.memory.channel` with Pydantic typing and an in-process cache so
reads don't re-fetch from Discord every time. On ``setup()`` the cache is primed
by reading every known doc once — after that, ``get`` is instant and ``set``
writes through to the channel and bumps the caption version.

Usage::

    store = Store(bot)
    await store.setup()
    profile = await store.get_or_default(Profile)
    profile.location = "Pune"
    await store.set(profile)
"""

from __future__ import annotations

import logging
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.memory import channel
from src.memory.schema import DOC_MODELS, LOG_MODELS
from src.util.dt import now_iso, today_iso

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

# Reverse lookup: model class -> doc name.
_NAME_BY_MODEL: dict[type[BaseModel], str] = {v: k for k, v in DOC_MODELS.items()}


class Store:
    def __init__(self, bot):
        self.client = bot
        self.channel = None
        # name -> (version, data, msg_id). msg_id 0 means not yet posted.
        self._cache: dict[str, tuple[int, dict, int]] = {}

    async def setup(self) -> None:
        self.channel = await channel.resolve_channel(self.client)
        await self._prime_cache()
        log.info("Memory store ready on channel #%s (id %s); cached %d docs.",
                 getattr(self.channel, "name", "?"),
                 getattr(self.channel, "id", "?"), len(self._cache))

    async def _prime_cache(self) -> None:
        for name in DOC_MODELS:
            rec = await channel.get_doc(self.client, self.channel, name)
            if rec:
                data, version, msg_id = rec
                self._cache[name] = (version, data, msg_id)

    # ── reads ────────────────────────────────────────────────────────────────

    async def get_raw(self, name: str) -> Optional[dict]:
        cached = self._cache.get(name)
        return cached[1] if cached else None

    async def get(self, model_cls: Type[M]) -> Optional[M]:
        name = _NAME_BY_MODEL.get(model_cls)
        if name is None:
            raise KeyError(f"{model_cls!r} is not a registered memory doc.")
        cached = self._cache.get(name)
        if not cached:
            return None
        return model_cls.model_validate(cached[1])

    async def get_or_default(self, model_cls: Type[M]) -> M:
        """Return the stored doc, or a fresh default instance if absent (not saved)."""
        doc = await self.get(model_cls)
        return doc if doc is not None else model_cls()

    async def reload(self, model_cls: Type[M]) -> None:
        """Re-read a doc from the channel and refresh the cache."""
        name = _NAME_BY_MODEL[model_cls]
        rec = await channel.get_doc(self.client, self.channel, name)
        if rec:
            data, version, msg_id = rec
            self._cache[name] = (version, data, msg_id)

    # ── writes ───────────────────────────────────────────────────────────────

    async def set(self, instance: BaseModel) -> int:
        """Persist a doc: bump version, upload to the channel, update cache."""
        name = _NAME_BY_MODEL.get(type(instance))
        if name is None:
            raise KeyError(f"{type(instance)!r} is not a registered memory doc.")
        old = self._cache.get(name)
        version = (old[0] if old else 0) + 1
        data = instance.model_dump(mode="json")
        msg_id = await channel.post_doc(
            self.client, self.channel, name, data, version, today_iso()
        )
        # tidy up: remove the previous version's message so the channel stays
        # lean and the doc scan window never drifts out of view.
        if old and old[2]:
            await channel.delete_message(self.client, self.channel, old[2])
        self._cache[name] = (version, data, msg_id)
        return msg_id

    # ── logs (append-only) ───────────────────────────────────────────────────

    async def append_log(self, kind: str, entry: BaseModel) -> int:
        if kind not in LOG_MODELS:
            raise KeyError(f"{kind!r} is not a registered log kind.")
        return await channel.append_log(
            self.client, self.channel, kind,
            entry.model_dump(mode="json"), now_iso(),
        )

    async def get_logs(self, kind: str, limit: int = 50, *, chronological: bool = False) -> list[BaseModel]:
        """Return recent log entries of ``kind`` (newest-first by default)."""
        model_cls = LOG_MODELS[kind]
        out: list[BaseModel] = []
        async for raw in channel.iter_logs(self.client, self.channel, kind, limit=limit):
            try:
                out.append(model_cls.model_validate(raw))
            except ValidationError as e:
                log.warning("Skipping malformed %s log entry: %s", kind, e)
        return list(reversed(out)) if chronological else out

    # ── introspection (for /show memory) ─────────────────────────────────────

    def cache_snapshot(self) -> dict[str, dict]:
        return {name: rec[1] for name, rec in self._cache.items()}

    def cached_version(self, model_cls: Type[BaseModel]) -> int:
        name = _NAME_BY_MODEL.get(model_cls)
        rec = self._cache.get(name) if name else None
        return rec[0] if rec else 0
