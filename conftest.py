"""Pytest root conftest.

Sets *dummy* Discord credentials into the environment before any test module
imports ``src.config`` (which eagerly validates required env vars at import
time). Real values from ``config/.env`` win because we use ``setdefault``.
"""

import os

os.environ.setdefault("DISCORD_BOT_TOKEN", "dummy:dummy")