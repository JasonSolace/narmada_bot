"""Environment-backed runtime configuration for the bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    """All configuration the runtime needs after `.env` has been loaded."""

    discord_token: str
    guild_id: int
    verified_role_name: str
    earthmc_api: str
    database_path: Path
    earthmc_requests_per_minute: int = 30
    retry_interval_hours: int = 24
    staff_role_name: Optional[str] = None
    verify_cooldown_seconds: int = 60
    verify_all_cooldown_seconds: int = 900


def load_settings() -> Settings:
    """Read required settings from the environment and normalize defaults."""

    missing = [
        name
        for name in ("DISCORD_TOKEN", "GUILD_ID", "VERIFIED_ROLE_NAME", "EARTHMC_API")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    project_root = Path(__file__).resolve().parent.parent
    return Settings(
        discord_token=os.environ["DISCORD_TOKEN"],
        guild_id=int(os.environ["GUILD_ID"]),
        verified_role_name=os.environ["VERIFIED_ROLE_NAME"],
        earthmc_api=os.environ["EARTHMC_API"].rstrip("/"),
        earthmc_requests_per_minute=_positive_int_from_env("EARTHMC_REQUESTS_PER_MINUTE", "30"),
        database_path=project_root / "verification.sqlite3",
        retry_interval_hours=int(os.getenv("RETRY_INTERVAL_HOURS", "24")),
        staff_role_name=os.getenv("STAFF_ROLE") or os.getenv("VERIFY_ALL_ROLE_NAME") or None,
        verify_cooldown_seconds=int(os.getenv("VERIFY_COOLDOWN_SECONDS", "60")),
        verify_all_cooldown_seconds=int(os.getenv("VERIFY_ALL_COOLDOWN_SECONDS", "900")),
    )


def _positive_int_from_env(name: str, default: str) -> int:
    """Parse positive integer env vars with a targeted error message."""

    raw_value = os.getenv(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value
