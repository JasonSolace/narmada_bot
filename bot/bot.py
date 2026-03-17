"""Discord entrypoint and workflow orchestration for the verification bot.

Architecture:
- `config.py` loads environment-backed settings.
- `earthmc_api.py` talks to EarthMC.
- `database.py` stores cached verification state.
- `scheduler.py` defines the retry cadence.
- This module ties those pieces together into Discord events and slash commands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Optional

import discord
from discord import app_commands

from bot.config import Settings, load_settings
from bot.database import VerificationRepository, utc_now_ms
from bot.earthmc_api import EarthMCApiClient, EarthMCApiError
from bot.scheduler import build_scheduler


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("narmada_verification_bot")


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a single verification attempt."""

    success: bool
    status: str
    minecraft_name: Optional[str] = None
    minecraft_uuid: Optional[str] = None


class VerificationBot(discord.Client):
    """Discord client that owns commands, scheduling, and member updates."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: VerificationRepository,
        earthmc_api: EarthMCApiClient,
        intents: discord.Intents,
    ) -> None:
        super().__init__(intents=intents)
        self.settings = settings
        self.repository = repository
        self.earthmc_api = earthmc_api
        # Scheduled retries and `/verify_all` share the same worker so they cannot overlap.
        self.scheduler = build_scheduler(self.retry_unverified_members, settings.retry_interval_hours)
        self.retry_lock = asyncio.Lock()
        # Command cooldowns are in-memory and reset when the process restarts.
        self.verify_cooldowns: dict[int, float] = {}
        self.verify_all_cooldown_until: float = 0.0
        # Commands are synced to one guild for fast propagation during development/admin use.
        self.command_guild = discord.Object(id=settings.guild_id)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(
            app_commands.Command(
                name="verify",
                description="Verify yourself, or verify another member if you have the staff role.",
                callback=self.verify_command,
            ),
            guild=self.command_guild,
        )
        self.tree.add_command(
            app_commands.Command(
                name="ign",
                description="Show the cached EarthMC IGN for a server member.",
                callback=self.ign_command,
            ),
            guild=self.command_guild,
        )
        self.tree.add_command(
            app_commands.Command(
                name="verify_all",
                description="STAFF ONLY: Manually retry verification for all unverified members.",
                callback=self.verify_all_command,
            ),
            guild=self.command_guild,
        )

    async def setup_hook(self) -> None:
        """Sync slash commands and start the recurring retry scheduler."""

        await self.tree.sync(guild=self.command_guild)
        self.scheduler.start()

    async def close(self) -> None:
        """Release shared resources during bot shutdown."""

        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.repository.close()
        await self.earthmc_api.close()
        await super().close()

    async def on_ready(self) -> None:
        """Log startup metadata once Discord has accepted the connection."""

        user_id = self.user.id if self.user else "unknown"
        logger.info("Logged in as %s (%s)", self.user, user_id)
        logger.info(
            (
                "Commands synced to guild %s; retry interval is every %s hours; "
                "EarthMC request limit is %s per minute; staff role is %s"
            ),
            self.settings.guild_id,
            self.settings.retry_interval_hours,
            self.settings.earthmc_requests_per_minute,
            self.settings.staff_role_name or "disabled",
        )

    async def on_member_join(self, member: discord.Member) -> None:
        """Auto-verify new members in the configured guild."""

        if member.bot or member.guild.id != self.settings.guild_id:
            return
        await self.attempt_verification(member, source="member_join")

    @app_commands.describe(member="Optional target member. Staff only when targeting someone else.")
    async def verify_command(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Slash command for users to trigger their own verification on demand."""

        if interaction.guild is None or interaction.guild.id != self.settings.guild_id:
            await interaction.response.send_message(
                "This command is only available in the configured server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Discord did not provide your server member data. Try again in the server.",
                ephemeral=True,
            )
            return

        if interaction.user.bot:
            await interaction.response.send_message(
                "Bots cannot use the verification command.",
                ephemeral=True,
            )
            return

        target = member or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "Discord did not provide that user's server member data.",
                ephemeral=True,
            )
            return

        if target.bot:
            await interaction.response.send_message(
                "Bots cannot be verified.",
                ephemeral=True,
            )
            return

        if target.id != interaction.user.id and not self._member_is_staff(interaction.user):
            await interaction.response.send_message(
                "Only members with the configured staff role can verify other users.",
                ephemeral=True,
            )
            return

        cooldown_message = self._check_verify_cooldown(target.id)
        if cooldown_message:
            await interaction.response.send_message(cooldown_message, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.attempt_verification(
            target,
            source="slash_verify_other" if target.id != interaction.user.id else "slash_verify",
        )
        await interaction.followup.send(
            self._format_verify_response(result, target=target, actor=interaction.user),
            ephemeral=True,
        )

    @app_commands.describe(member="The server member to check.")
    async def ign_command(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Return the cached IGN from SQLite for a verified user."""

        if interaction.guild is None or interaction.guild.id != self.settings.guild_id:
            await interaction.response.send_message(
                "This command is only available in the configured server.",
                ephemeral=True,
            )
            return

        target = member or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "Discord did not provide that user's server member data.",
                ephemeral=True,
            )
            return

        if target.bot:
            await interaction.response.send_message(
                "Bots do not have EarthMC verification data.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self._lookup_ign_message(target)
        await interaction.followup.send(message, ephemeral=True)

    async def retry_unverified_members(self) -> None:
        """Scheduled retry entrypoint used by APScheduler."""

        await self.wait_until_ready()
        attempted_count, verified_count = await self._run_retry_pass(source="daily_retry")
        logger.info(
            "Daily retry completed for %s unverified members; %s were verified",
            attempted_count,
            verified_count,
        )

    async def verify_all_command(self, interaction: discord.Interaction) -> None:
        """Staff-only slash command to run the retry pass immediately."""

        if interaction.guild is None or interaction.guild.id != self.settings.guild_id:
            await interaction.response.send_message(
                "This command is only available in the configured server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Discord did not provide your server member data. Try again in the server.",
                ephemeral=True,
            )
            return

        required_role_name = self.settings.staff_role_name
        if not required_role_name:
            await interaction.response.send_message(
                "Manual bulk verification is disabled because STAFF_ROLE is not configured.",
                ephemeral=True,
            )
            return

        if not self._member_has_role(interaction.user, required_role_name):
            await interaction.response.send_message(
                f"You need the `{required_role_name}` role to use `/verify_all`.",
                ephemeral=True,
            )
            return

        cooldown_message = self._check_verify_all_cooldown()
        if cooldown_message:
            await interaction.response.send_message(cooldown_message, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        attempted_count, verified_count = await self._run_retry_pass(source="manual_verify_all")
        await interaction.followup.send(
            (
                f"Verification pass complete. Checked {attempted_count} unverified members and "
                f"verified {verified_count} of them."
            ),
            ephemeral=True,
        )
        self._start_verify_all_cooldown()

    async def attempt_verification(self, member: discord.Member, source: str) -> VerificationResult:
        """Run the full EarthMC lookup flow and cache the resulting state."""

        checked_at = utc_now_ms()
        try:
            uuid = await self.earthmc_api.resolve_discord_link(member.id)
            if not uuid:
                self.repository.record_check(member.id, verified=False, checked_at=checked_at)
                logger.info("No EarthMC discord link for %s during %s", member.id, source)
                return VerificationResult(success=False, status="not_linked")

            player = await self.earthmc_api.fetch_player(uuid)
            ign = self._extract_ign(player, fallback_uuid=uuid)
            self.repository.record_check(
                member.id,
                verified=True,
                minecraft_uuid=uuid,
                minecraft_name=ign,
                checked_at=checked_at,
            )
            await self._apply_member_updates(member, ign)
            logger.info("Verified Discord user %s as %s during %s", member.id, ign or uuid, source)
            return VerificationResult(
                success=True,
                status="verified",
                minecraft_name=ign,
                minecraft_uuid=uuid,
            )
        except EarthMCApiError:
            # API outages should not erase a previously verified cache entry.
            self.repository.touch_check(member.id, checked_at=checked_at)
            logger.exception("EarthMC API failure while checking %s during %s", member.id, source)
            return VerificationResult(success=False, status="api_error")
        except discord.HTTPException:
            logger.exception("Discord update failure while checking %s during %s", member.id, source)
            return VerificationResult(success=False, status="discord_error")
        except Exception:
            self.repository.touch_check(member.id, checked_at=checked_at)
            logger.exception("Unexpected verification failure for %s during %s", member.id, source)
            return VerificationResult(success=False, status="unexpected_error")

    async def _apply_member_updates(self, member: discord.Member, ign: Optional[str]) -> None:
        """Apply Discord-side changes after a successful EarthMC match."""

        verified_role = self._get_verified_role(member.guild)
        if verified_role and verified_role not in member.roles:
            try:
                await member.add_roles(verified_role, reason="EarthMC automatic verification")
            except discord.Forbidden:
                logger.info("Missing permission to add verified role for %s", member.id)
            except discord.HTTPException:
                logger.warning("Failed to add verified role for %s", member.id, exc_info=True)
        elif verified_role is None:
            logger.warning(
                "Verified role '%s' was not found in guild %s",
                self.settings.verified_role_name,
                member.guild.id,
            )

        if not ign or member.nick == ign:
            return

        try:
            await member.edit(nick=ign, reason="EarthMC automatic verification")
        except discord.Forbidden:
            logger.info("Missing permission to update nickname for %s", member.id)
        except discord.HTTPException:
            logger.warning("Failed to update nickname for %s", member.id, exc_info=True)

    def _get_verified_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        """Look up the configured verified role by name inside the guild."""

        return discord.utils.get(guild.roles, name=self.settings.verified_role_name)

    async def _run_retry_pass(self, source: str) -> tuple[int, int]:
        """Scan the guild for members missing the verified role and retry them."""

        async with self.retry_lock:
            guild = self.get_guild(self.settings.guild_id)
            if guild is None:
                logger.warning("Configured guild %s is not available to the bot", self.settings.guild_id)
                return (0, 0)

            verified_role = self._get_verified_role(guild)
            attempted_count = 0
            verified_count = 0

            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue
                if verified_role and verified_role in member.roles:
                    continue

                attempted_count += 1
                result = await self.attempt_verification(member, source=source)
                if result.success:
                    verified_count += 1

            return (attempted_count, verified_count)

    def _format_verify_response(
        self,
        result: VerificationResult,
        *,
        target: discord.Member,
        actor: discord.abc.User,
    ) -> str:
        """Convert a verification result into a short user-facing status message."""

        subject = "You were" if target.id == actor.id else f"{target.display_name} was"
        owner = "your account" if target.id == actor.id else f"{target.display_name}'s account"

        if result.success:
            if result.minecraft_name:
                return (
                    f"{subject} verified successfully. The Discord nickname should now match "
                    f"`{result.minecraft_name}` and the `{self.settings.verified_role_name}` role was applied."
                )
            return (
                f"{subject} verified successfully. The `{self.settings.verified_role_name}` role was applied, "
                f"but EarthMC did not return a player name for {owner}."
            )

        if result.status == "not_linked":
            return (
                f"No EarthMC Discord link was found for {owner}. "
                "Use `/discord link` in EarthMC first, then run `/verify` again."
            )

        if result.status == "api_error":
            return "EarthMC API is unavailable right now. Try `/verify` again later."

        if result.status == "discord_error":
            return (
                "Your EarthMC account was found, but Discord rejected part of the update. "
                "Check the bot role position and nickname permissions."
            )

        return "Verification failed unexpectedly. Check the bot logs and try again."

    async def _lookup_ign_message(self, member: discord.Member) -> str:
        """Serve `/ign` directly from the bot's SQLite cache."""

        record = self.repository.get_verified_record(member.id)
        if record is None:
            return "User is not verified on EarthMC."
        if record.minecraft_name:
            return f"{member.display_name}'s EarthMC IGN is `{record.minecraft_name}`."
        return (
            f"{member.display_name} is verified in the local cache, "
            "but no IGN is stored yet. Run `/verify` again to refresh it."
        )

    @staticmethod
    def _member_has_role(member: discord.Member, role_name: str) -> bool:
        """Role-name gate used by `/verify_all`."""

        return discord.utils.get(member.roles, name=role_name) is not None

    def _member_is_staff(self, member: discord.Member) -> bool:
        """Check whether a member has the configured staff role."""

        role_name = self.settings.staff_role_name
        return bool(role_name) and self._member_has_role(member, role_name)

    def _check_verify_cooldown(self, target_member_id: int) -> Optional[str]:
        """Enforce a per-target cooldown for manual `/verify` requests."""

        now = time.monotonic()
        expires_at = self.verify_cooldowns.get(target_member_id, 0.0)
        if expires_at > now:
            remaining = max(1, int(expires_at - now))
            return f"`/verify` is on cooldown for this user. Try again in about {remaining} seconds."
        self.verify_cooldowns[target_member_id] = now + self.settings.verify_cooldown_seconds
        return None

    def _check_verify_all_cooldown(self) -> Optional[str]:
        """Enforce the global cooldown for manual `/verify_all` runs."""

        now = time.monotonic()
        if self.verify_all_cooldown_until > now:
            remaining_seconds = max(1, int(self.verify_all_cooldown_until - now))
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            return (
                "`/verify_all` is on cooldown. "
                f"Try again in about {remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}."
            )
        return None

    def _start_verify_all_cooldown(self) -> None:
        """Start the cooldown window after a successful manual `/verify_all` run."""

        self.verify_all_cooldown_until = time.monotonic() + self.settings.verify_all_cooldown_seconds

    @staticmethod
    def _extract_ign(player: Optional[dict], fallback_uuid: str) -> Optional[str]:
        """Prefer the player name; fall back to a changed UUID payload if needed."""

        if not player:
            return None
        name = player.get("name")
        if name:
            return str(name)
        uuid = player.get("uuid")
        if uuid and str(uuid) != fallback_uuid:
            return str(uuid)
        return None


def main() -> None:
    """Build runtime dependencies and start the Discord client."""

    settings = load_settings()
    repository = VerificationRepository(settings.database_path)
    repository.initialize()

    intents = discord.Intents.default()
    intents.members = True

    client = VerificationBot(
        settings=settings,
        repository=repository,
        earthmc_api=EarthMCApiClient(
            settings.earthmc_api,
            requests_per_minute=settings.earthmc_requests_per_minute,
        ),
        intents=intents,
    )
    client.run(settings.discord_token)


if __name__ == "__main__":
    main()
