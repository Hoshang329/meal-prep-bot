# Meal-Prep Bot

A personal Discord automation that plans your weekly meal-prep, grills you during
onboarding to learn your tastes + local market prices, sends a weekly menu for
approval, a grocery list, batch-prep instructions, and a daily menu — then asks
"did you cook?" and **learns from your answers**.

Memory lives **in a private Discord text channel only you + the bot can see** —
there is no server-side database. A small always-on process (VPS / Raspberry Pi /
Termux / serverless-cron) runs the bot, reads & writes that channel, and calls an
LLM to plan.

```
              YOU (Discord client, on your phone / desktop)
              ┌──────────────────────────────────────────────────┐
   you ─────▶ │  DM with the Bot (slash commands + inline buttons) │
   bot replies│ A private text channel "MealPrepMemory"  (= the DB) │ ◀── memory
              └────────────────────┬─────────────────────────────┘
                                   │ Discord Gateway
                                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ ONE PYTHON PROCESS  (discord.py)                             │
   │  Bot client (chat UX + slash/cmds + component router)         │
   │           →  Memory store  →  Planner  →  Scheduler           │
   │           →  Feedback/Learn  →  LLM layer                    │
   └──────────────────────────────┬───────────────────────────────┘
                                  ▼
              OpenCode API → MiniMax M3 Free (swappable)
```

**Why one client?** Discord's bot API — unlike Telegram's Bot API — **can read a
channel's message history**. That removes the entire two-client dance the old
Telegram version needed (a BotFather bot for chat + a personal MTProto account
just to own a readable memory channel). Here, one discord.py bot both chats and
owns the memory channel.

---

## Setup

### 1. Create the Discord application + bot
1. Go to <https://discord.com/developers/applications> → **New Application**.
   Give it a name (e.g. "Meal-Prep Bot").
2. In your application: **Bot** → **Reset Token** → copy the token (this is
   `DISCORD_BOT_TOKEN`).
3. Under **Privileged Gateway Intents**, enable:
   - **MESSAGE CONTENT INTENT** (so the bot can read your free-text replies in a
     server text channel — DMs to the bot are always readable, but enabling this
     lets you chat in either a server channel or a DM).
   - SERVER MEMBERS INTENT is **not** required.
4. Invite the bot to a server you own. In **OAuth2 → URL Generator**, select
   scopes **`bot`** + **`applications.commands`**, and Bot Permissions: **View
   Channels, Send Messages, Read Message History, Attach Files, Embed Links,
   Manage Messages** (the last only so it can clear its own component buttons).

### 2. Create the memory channel + lock the owner
1. In that server, create a **private text channel** named `MealPrepMemory`
   (or anything — set `MEMORY_CHANNEL_NAME` to match). Grant your own account +
   the bot access. The bot only uses this channel as a database; you don't read
   it daily.
2. Get the **channel id**: enable **Developer Mode** in Discord
   (*Settings → Advanced → Developer Mode*), right-click the channel → **Copy ID**
   → put it in `MEMORY_CHANNEL`. (Leave blank + use
   `MEMORY_CHANNEL_NAME` to auto-detect by name instead.)
3. Get **your own user id** the same way (right-click your own name → Copy ID) →
   put it in `OWNER_DISCORD_ID`. This permanently locks the bot to your account
   so a restart can't be hijacked.

### 3. LLM credentials
Set up your OpenCode API key + base URL (OpenAI-compatible). Default model is
`minimax-m3-free`. **Heads-up:** the free MiniMax M3 Free promotion may have
ended — if you see a `401 "Free promotion has ended"`, switch `LLM_MODEL` to
another available model or subscribe to OpenCode Go. The architecture is
unchanged either way. Any OpenAI-compatible endpoint (OpenAI, Ollama, …) works.

### 4. Configure
```bash
cd mealprep-bot
python -m venv .venv
# Windows:  .venv\Scripts\activate
# POSIX:    source .venv/bin/activate
pip install -r requirements.txt
copy config\.env.example config\.env   # Windows
# cp config/.env.example config/.env   # POSIX
```
Edit `config/.env` and fill in all values.

### 5. Run
```bash
python -m src.main
```
On first run, the bot connects to Discord, syncs slash commands (may take a few
minutes to appear globally on first run), and primes the memory cache from the
private channel. Then message your bot `/start` (in DM or the server channel) to
begin onboarding.

---

## Commands (chat with the bot)

| Command | What it does |
|---|---|
| `/start` | Begin (or resume) onboarding, then plan the first week. |
| `/plan` | Generate/revise this week's menu → Approve / Request changes. |
| `/today` | Send today's menu + cooking instructions. |
| `/prep` | Send the batch-prep task list for prep day. |
| `/feedback` | Ask "did you cook today's food?" (Yes / Made something else / Skipped). |
| `/price <item> <price>/<unit>` | Correct a market price, e.g. `/price rice 90/kg`. |
| `/learn` | Re-distil your feedback into learnings now. |
| `/recipes` | Show your recipe library (fills as you approve weekly plans). |
| `/show memory` | Dump current memory (proves it round-trips from Discord). |
| `/show <doc>` | Dump one memory doc, e.g. `/show profile`. |
| `/init` | Re-resolve the memory channel (use if you recreate the channel). |
| `/cancel` | Cancel a pending plan/grocery change or feedback reason follow-up. |
| `/help` | List commands. |

Free text also works anytime (in DM to the bot, or in a server channel it can
see): *"rice is now ₹90/kg"*, *"I hated Tuesday's dinner"*, *"add 2 eggs to the
grocery list"*.

---

## How the memory works

Memory is JSON stored as **file attachments in your private Discord channel**.
Each attachment's message text ("caption") tags it `#doc:<name> v<n> <date>`;
latest-by-caption wins, and previous versions are deleted on update so the
channel stays lean and the doc scan window never drifts. Append-only logs
(feedback, plan history, plan_history archive entries) are plain tagged messages
(`#log:<kind> <ts>`). Docs:

- `profile.json` — preliminary onboarding facts (location, budget, diet, skill,
  equipment, prep day, meal times, storage, cuisines, dislikes …)
- `preferences.json` — derived this-or-that preferences
- `prices.json` — your local market prices `{item: {price, currency, unit, per, updated}}`
- `pantry.json` — what's already stocked (now actually fed into the weekly plan)
- `current_plan.json` — the active week + grocery list + estimated cost + status
- `recipe_library.json` — approved recipes (latest version wins on overwrite)
- `learnings.json` — insights distilled from feedback (consumed by the next plan)
- `onboarding_state.json` — resumable onboarding progress
- `owner.json` — id of the account allowed to command the bot (only written when
  `OWNER_DISCORD_ID` env is unset; env wins permanently)
- logs: `feedback`, `plan_history` (append-only)

The **learn loop**: every "did you cook?" answer → `feedback` log → a periodic
job has the LLM distil recent feedback into `learnings.json` → the next weekly
plan applies those learnings. Memory improves week over week.

---

## Scheduling

A single process runs APScheduler cron jobs in your `TIMEZONE` (override times
with `CRON_*` env vars — see `config/.env.example`):

| Job | Default | What it does |
|---|---|---|
| `weekly_plan` | Sun 09:00 | Plan the *upcoming* week, send menu for approval. |
| `daily_menu` | 08:00 daily | Send today's menu + instructions (silent outside the plan window). |
| `prep_day` | 08:05 daily | On your configured prep day, send the batch-prep task list. |
| `feedback` | 20:00 daily | "Did you cook today?" ping (silent if nothing was planned). |
| `learn` | 23:00 daily | Distil recent feedback into `learnings.json`. |

When the weekly plan regenerates an already-approved plan, the previous approved
plan is **archived to `plan_history`** before overwrite (so its record isn't lost).

---

## Development & tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The pure-logic layers (`util/prices`, `util/units`, grocery math, daily formatting,
memory schema round-trip) are unit-tested without Discord or LLM access.

## Project layout
```
src/
  config.py              # typed .env settings
  app.py                 # Ctx: shared clients/store/scheduler container
  main.py                # build the Discord bot, wire ctx, start scheduler
  chat/                  # discord.py client, slash commands, inline buttons, send helpers
  memory/                # schema, low-level Discord channel IO, high-level store
  onboarding/            # preliminary + LLM-derived adaptive flow
  planner/               # prompts, weekly/grocery/prep/daily, swappable LLM
  feedback/              # did-you-cook loop + learn distillation
  recipes/               # recipe library cache
  scheduler/             # APScheduler cron jobs
  util/                  # price parsing, unit/servings math, datetime helpers
tests/                   # unit tests for pure logic
```

---

## Notes on the Telegram → Discord migration

- **One client instead of two.** A Discord bot can read channel message history,
  so the old "MTProto user account owns the memory channel + BotFather bot for
  chat" split collapses into a single discord.py bot.
- **Markdown.** Discord uses `**bold**` and `*italic*` (Telegram used `*bold*`
  and `_italic_`). All bot output was translated accordingly.
- **Inline keyboards → buttons.** Each Telegram inline button became a
  `discord.ui.Button` sharing a small router; the same `ob`/`obm`/`app`/`groc`/
  `fb`/`yn` custom_id scheme carries over. Multi-choice toggles now **edit the
  picker message in place** (Discord interaction responses allow that) instead of
  spamming a new message per toggle.
- **Owner lock is persisted.** Previously `ctx.owner_id` lived only in memory
  and the first user to DM after a restart hijacked the bot. Now the env
  `OWNER_DISCORD_ID` always wins; if unset, the first DM writes a permanent
  `owner.json` doc to the memory channel.

## Bug fixes applied alongside this migration

- `grocery.trim_to_budget` / `grocery.revise` now pass a strict Pydantic schema
  to `llm.chat_json` (previously relied on "Output ONLY JSON" prompt only).
- `weekly_plan.generate` now actually feeds the `pantry` to the LLM prompt (the
  parameter was accepted but silently ignored; the LLM planned blind to your
  stocked staples).
- `recipes.ingest_plan` now overwrites an existing recipe with the newer version
  (previously first-wins left stale recipes forever).
- The memory-doc scan window is now configurable + generous (default 400, was 80)
  and docs are deleted on overwrite, so a long-running bot can't lose docs
  past the scan window.
- Approved plans are archived to `plan_history` when regenerated (previously
  only explicit Approve logged history; a re-plan silently overwrote).
- `/cancel` now clears pending feedback-reason follow-ups too.
- `_tz()` logs a one-time warning instead of silently falling back to UTC when
  the configured timezone name is invalid.
- `daily.format_day`/`format_today`'s `profile` parameter is now actually used
  (meal-time hints from `profile.meal_times`) instead of being dead.