# Architecture Guide

This document explains how the bot is structured for developers who are new to the project.

## High-Level Flow

The bot has four runtime responsibilities:

1. Load configuration from `.env`
2. Talk to EarthMC for verification lookups
3. Cache verification state in SQLite
4. Expose Discord events and slash commands

Each responsibility is isolated into a small module so changes stay local.

## Module Map

### `bot/config.py`

Loads environment variables into a single `Settings` dataclass. This is the only place that knows how `.env` maps into runtime configuration.

### `bot/earthmc_api.py`

Wraps EarthMC HTTP requests. Discord code should not build raw JSON payloads or manage HTTP errors directly; it asks this module for:

- Discord ID -> Minecraft UUID
- Minecraft UUID -> player payload
- global EarthMC request throttling and 429 backoff handling

### `bot/database.py`

Owns the SQLite schema and cached verification records.

Important detail:
- successful verification writes authoritative cached state
- transient API failures do not clear previously verified cache rows

That behavior matters because `/ign` now uses SQLite only.

### `bot/scheduler.py`

Builds the APScheduler instance used for retry jobs. It does not know anything about Discord or EarthMC beyond "call this async job every N hours".

### `bot/bot.py`

This is the orchestration layer. It wires config, database, scheduler, and API access into:

- `on_member_join`
- `/verify`
- `/ign`
- `/verify_all`
- scheduled retry runs

## Command Behavior

### `/verify`

Runs a live EarthMC verification for the calling member.

Staff behavior:
- members with `STAFF_ROLE` can target another user with `/verify member:@user`
- non-staff users can only verify themselves

Effects:
- queries EarthMC
- updates SQLite
- assigns the verified role
- attempts to set Discord nickname to the cached IGN
- uses a per-target manual cooldown to limit spam

### `/ign`

Reads only from SQLite.

Implications:
- fast and does not call EarthMC
- returns only what this bot has already cached
- if a user has never been verified by this bot, `/ign` will say they are not verified

### `/verify_all`

Runs the same retry pass as the scheduler, but only for members with the configured `STAFF_ROLE`.

Important detail:
- scheduled retries do not use the manual cooldown
- manual `/verify_all` runs have a global cooldown window

## Verification Lifecycle

### Member Join

1. Discord fires `on_member_join`
2. Bot asks EarthMC whether the member's Discord account is linked
3. If linked, bot fetches player info and caches it
4. Bot adds the verified role and updates nickname

### Scheduled Retry

1. Scheduler starts when the bot starts
2. First retry is delayed about one minute
3. After that, retries repeat every `RETRY_INTERVAL_HOURS`
4. Only members missing the verified role are retried

### Manual Retry

`/verify_all` reuses the same retry worker as the scheduler. A shared async lock prevents the scheduled retry and manual bulk retry from running at the same time.

## SQLite Data Model

Table: `verifications`

Columns:
- `discord_id`
- `minecraft_uuid`
- `minecraft_name`
- `verified`
- `first_seen_at`
- `last_checked_at`

Interpretation:
- `verified = 1` means the bot has a cached positive verification result
- `verified = 0` means the bot has either never confirmed the user or last saw a negative result
- temporary API outages should only update `last_checked_at`, not flip verified users to false

## Design Tradeoffs

### Why `/ign` Uses SQLite Instead of EarthMC

This was chosen intentionally to make the command:

- fast
- independent of EarthMC uptime
- aligned with the bot's own cached verification state

Tradeoff:
- `/ign` is only as current as the last successful verification stored by this bot

### Why Guild-Scoped Slash Commands

Commands are synced only to the configured guild so updates appear quickly and stay scoped to the server the bot manages.

## Safe Places To Extend

If you add a new command:
- put EarthMC HTTP details in `earthmc_api.py`
- put persistent state changes in `database.py`
- keep Discord command logic in `bot.py`

If you add new configuration:
- define it in `Settings`
- load it in `load_settings()`
- document it in `README.md` and `.env.example`
