# AGENTS.md — repo-specific guidance for AI coding agents

This file tells any AI agent (or human contributor) working in this repo what to
run to keep the codebase clean.

## Project

A Discord bot that plans weekly meal-prep for one user. Memory lives in a private
Discord text channel only the bot + owner can see — there is no server-side database.
Stack: discord.py, APScheduler, httpx (OpenAI-compatible LLM), pydantic v2.
See `README.md` for the full architecture.

## Required commands after editing Python

Run these from the project root after *every* Python edit before declaring a task
done. They're fast (sub-second) and the only way to keep quality from rotting.

```bash
# Lint — real-bug checks only (unused imports, undefined names, `== None`, etc.).
# Lenient by design: does NOT enforce line length or style. See ruff.toml.
ruff check src tests

# Tests — pure-logic layers (util, grocery, daily, schema, dt). No Discord / LLM /
# network access required. Should always pass.
pytest -q
```

Both should pass with zero findings/failures. If you add a new module, run `ruff`
on it implicitly covers it via the `src`/`tests` glob.

## Layout map (where things live)

- `src/main.py` — entrypoint. Wires the discord.py bot + scheduler.
- `src/chat/` — Discord bot, slash commands, button components, send helpers.
- `src/memory/` — pydantic schemas, low-level Discord channel IO, typed store.
- `src/onboarding/` — preliminary script + LLM-derived adaptive questions.
- `src/planner/` — prompts, weekly/grocery/prep/daily, swappable LLM (llm.py).
- `src/feedback/` — did-you-cook loop + nightly learn distillation.
- `src/recipes/` — approved-meal cache library.
- `src/scheduler/` — APScheduler cron jobs.
- `src/util/` — prices parsing, unit math, tz-aware datetime helpers.

## Conventions

- All user-facing strings use **Discord markdown**: `**bold**`, `*italic*`, not the
  Telegram `*bold*`/`_italic_` flavour.
- Memory docs are pydantic v2 models in `src/memory/schema.py` and registered in
  `DOC_MODELS` (editable docs) or `LOG_MODELS` (append-only logs). Always go
  through `ctx.store.set(...)` / `ctx.store.append_log(...)` — never write to the
  channel directly.
- All datetime values come from `src/util/dt.py` (tz-aware). Never call
  `datetime.now()` raw elsewhere.
- All LLM calls funnel through `src/planner/llm.py:chat`/`chat_json`. If you add
  a structured-output LLM call, pass a pydantic model (or dict schema) so
  `response_format={"type":"json_object"}` and the strict-JSON instruction both
  kick in — don't rely on "output ONLY JSON" prompt text alone.
- `ctx.owner_id` is loaded at startup from `OWNER_DISCORD_ID` (env, wins) or the
  `owner` memory doc, and is *persisted* to that doc on first DM when the env is
  unset. Never reset it to `None` on restart.

## Known constraints

- No async tests (no `pytest-asyncio` dep). Don't write tests that `await` things
  without a fixture that drives the loop.
- The memory channel scan window defaults to 400 messages (env `MEMORY_DOC_SCAN`).
  Docs are deleted on overwrite, so logs are the main channel-grower.
- Discord slash-command global propagation takes a few minutes on first run —
  not a bug, just a Discord behaviour. Re-running won't speed it up.