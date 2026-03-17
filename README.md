# Narmada EarthMC Verification Bot

Discord bot for automatic EarthMC account verification.

When a member joins your Discord server, the bot checks whether that Discord account is linked in EarthMC through `/discord link`. If a match exists, it assigns your verified role and changes the member nickname to their Minecraft IGN. Members who are still unverified are retried on a schedule while the bot is running.

This version is verification-only. It does not implement whitelist syncing or town/nation role logic.

For a developer-oriented walkthrough of the codebase, see [`ARCHITECTURE.md`](C:/Users/Jason/Documents/code/narmada_bot/ARCHITECTURE.md).

## Requirements

- Python 3.9 or newer
- A Discord bot token
- A Discord server where the bot has:
  - `Manage Roles`
  - `Manage Nicknames`
  - `Read Messages`
  - `Send Messages`
  - `View Channels`
- `Server Members Intent` enabled for the bot in the Discord Developer Portal

This project should be installed from the pinned [`requirements.txt`](C:/Users/Jason/Documents/code/narmada_bot/requirements.txt). Using unpinned latest packages on Python 3.9 can pull incompatible `aiohttp` versions and prevent `discord.py` from importing.

## Install

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

## Discord Setup

1. Create a bot application in the Discord Developer Portal.
2. Copy the bot token.
3. Under `Bot`, enable `Server Members Intent`.
4. Invite the bot to your server with the required permissions.
5. Create this role in your server:
   - `EarthMC Verified`
6. Make sure the bot's role is higher than `EarthMC Verified` in the server role list, or Discord will reject the role assignment.
7. Reinvite the bot if needed with the `applications.commands` scope so the slash commands can appear.

## Configuration

Create a `.env` file in the project root.

```env
DISCORD_TOKEN=your_bot_token
GUILD_ID=123456789012345678
VERIFIED_ROLE_NAME=EarthMC Verified
EARTHMC_API=https://api.earthmc.net/v3/aurora
EARTHMC_REQUESTS_PER_MINUTE=30
RETRY_INTERVAL_HOURS=24
STAFF_ROLE=Moderator
VERIFY_COOLDOWN_SECONDS=60
VERIFY_ALL_COOLDOWN_SECONDS=900
```

Notes:

- `DISCORD_TOKEN`: your Discord bot token
- `GUILD_ID`: the numeric ID of the Discord server the bot should manage
- `VERIFIED_ROLE_NAME`: role granted after successful EarthMC verification
- `EARTHMC_API`: EarthMC API base URL; Aurora is the current value used by this bot
- `EARTHMC_REQUESTS_PER_MINUTE`: global EarthMC request cap used by the bot; default is `30`
- `RETRY_INTERVAL_HOURS`: how often the bot retries unverified members; default is `24`
- `STAFF_ROLE`: optional Discord role that can run `/verify_all` and verify other members with `/verify`
- `VERIFY_COOLDOWN_SECONDS`: per-user manual `/verify` cooldown; default is `60`
- `VERIFY_ALL_COOLDOWN_SECONDS`: manual `/verify_all` cooldown; default is `900` seconds
- If Discord rejects login with `401 Unauthorized`, the token in `DISCORD_TOKEN` is invalid or expired

Rate-limit note:

- As of March 17, 2026, EarthMC's public API docs at `https://earthmc.net/docs/api` do not publish a numeric rate limit.
- A live header check against `https://api.earthmc.net/v3/aurora/` also did not expose `X-RateLimit-*` headers.
- This bot therefore defaults to a conservative inferred cap of `30` requests per minute, sends requests serially, and respects `Retry-After` if EarthMC returns `429 Too Many Requests`.

## Run

From the project root:

```powershell
python -m bot.bot
```

## Commands

- `/verify`: live EarthMC lookup for yourself; staff can also target another member
- `/ign`: SQLite-only cached lookup for yourself or another member
- `/verify_all`: staff-triggered bulk retry for unverified members; requires `STAFF_ROLE`

## How It Works

- On member join, the bot calls EarthMC `POST /discord` with the Discord user ID.
- Members can also run `/verify` in the configured server to trigger the same verification flow on demand.
- Staff can run `/verify member:@user` to verify someone else manually.
- Members can run `/ign` to look up their own cached IGN, or `/ign member:@user` to look up someone else from SQLite.
- Members with the configured `STAFF_ROLE` role can run `/verify_all` to manually retry all unverified members.
- If EarthMC returns a Minecraft UUID, the bot calls `POST /players` to fetch the player's IGN.
- The bot adds the verified role and tries to set the member nickname to that IGN.
- If nickname updates fail because of Discord permissions, the bot logs the failure and continues.
- All EarthMC requests are serialized through one shared limiter so join events, slash commands, and retries cannot spike the API at once.
- Manual `/verify` requests are rate-limited per target user to reduce spam.
- Manual `/verify_all` requests are rate-limited globally.
- On startup, the bot schedules a retry job and runs the first retry about one minute later.
- After that, it retries unverified members every `RETRY_INTERVAL_HOURS`.

## Data Storage

The bot stores verification state in a local SQLite database:

- `verification.sqlite3`

Stored fields:

- `discord_id`
- `minecraft_uuid`
- `minecraft_name`
- `verified`
- `first_seen_at`
- `last_checked_at`

Interpretation:

- `/verify` writes authoritative cache entries after live EarthMC checks
- `/ign` only reads the local cache
- transient API failures should not erase a previously verified cache row

## Project Structure

```text
ARCHITECTURE.md
bot/
  bot.py
  config.py
  database.py
  earthmc_api.py
  scheduler.py
```

## Architecture Summary

- [`bot/config.py`](C:/Users/Jason/Documents/code/narmada_bot/bot/config.py): loads `.env` into a single settings object
- [`bot/earthmc_api.py`](C:/Users/Jason/Documents/code/narmada_bot/bot/earthmc_api.py): EarthMC HTTP client
- [`bot/database.py`](C:/Users/Jason/Documents/code/narmada_bot/bot/database.py): SQLite cache and record access
- [`bot/scheduler.py`](C:/Users/Jason/Documents/code/narmada_bot/bot/scheduler.py): APScheduler setup
- [`bot/bot.py`](C:/Users/Jason/Documents/code/narmada_bot/bot/bot.py): Discord events, slash commands, and orchestration

## Operational Notes

- The bot only acts inside the configured `GUILD_ID`.
- The `/verify`, `/ign`, and `/verify_all` slash commands are synced only to the configured `GUILD_ID`.
- Bot accounts are ignored.
- If EarthMC does not return a linked account, the member remains unverified and will be retried later.
- `/ign` only reads from SQLite, so it reflects what this bot has already cached rather than live EarthMC state.
- Scheduled retries are not blocked by the manual command cooldowns.
- If the EarthMC API is unavailable, the bot logs the error and retries on the next scheduled run.
- The first scheduled retry runs about one minute after startup, then every `RETRY_INTERVAL_HOURS` after that.

## Troubleshooting

If the bot starts but does not verify anyone:

- Confirm the member used EarthMC `/discord link`
- Confirm `Server Members Intent` is enabled
- Confirm `GUILD_ID` is correct
- Confirm the verified role name exactly matches `VERIFIED_ROLE_NAME`
- Confirm the bot role is above the verified role in Discord
- Confirm the bot has `Manage Roles` and `Manage Nicknames`
- Confirm `DISCORD_TOKEN` is the current bot token from the Discord Developer Portal

If `/ign` says a user is not verified but you expect a result:

- Confirm the user has already been verified by this bot
- Run `/verify` for that user first so the SQLite cache is populated
- Remember that `/ign` does not call EarthMC anymore

If the bot cannot change nicknames:

- Check the bot has `Manage Nicknames`
- Check the target user's highest role is below the bot's highest role

If the bot cannot assign the verified role:

- Check the role name matches
- Check the bot role is above the verified role
- Check the bot has `Manage Roles`

If the bot fails during import or startup on Python 3.9:

- Recreate the virtual environment
- Install with `pip install -r requirements.txt`
- Do not replace the pinned dependencies with unpinned latest versions unless you also retest compatibility
