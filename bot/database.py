"""SQLite persistence for verification state.

The bot keeps one row per Discord user. Successful verification writes
authoritative EarthMC data, while transient failures only update the
last-checked timestamp so cached lookups remain useful.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from pathlib import Path
from typing import Optional


def utc_now_ms() -> int:
    """Return the current UTC timestamp in milliseconds."""

    return int(time.time() * 1000)


@dataclass(frozen=True)
class VerificationRecord:
    """Cached verification state for a single Discord user."""

    discord_id: str
    minecraft_uuid: Optional[str]
    minecraft_name: Optional[str]
    verified: bool
    first_seen_at: int
    last_checked_at: int


class VerificationRepository:
    """Thin repository around the bot's single SQLite table."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._connection = sqlite3.connect(self._database_path)
        self._connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        """Create the verification table if this is the first run."""

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS verifications (
                discord_id TEXT PRIMARY KEY,
                minecraft_uuid TEXT,
                minecraft_name TEXT,
                verified INTEGER NOT NULL DEFAULT 0,
                first_seen_at INTEGER NOT NULL,
                last_checked_at INTEGER NOT NULL
            )
            """
        )
        self._connection.commit()

    def record_check(
        self,
        discord_id: int,
        verified: bool,
        minecraft_uuid: Optional[str] = None,
        minecraft_name: Optional[str] = None,
        checked_at: Optional[int] = None,
    ) -> None:
        """Persist an authoritative verification outcome.

        Use this when EarthMC returned a definite yes/no answer.
        """

        timestamp = checked_at or utc_now_ms()
        self._connection.execute(
            """
            INSERT INTO verifications (
                discord_id,
                minecraft_uuid,
                minecraft_name,
                verified,
                first_seen_at,
                last_checked_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                minecraft_uuid = COALESCE(excluded.minecraft_uuid, verifications.minecraft_uuid),
                minecraft_name = COALESCE(excluded.minecraft_name, verifications.minecraft_name),
                verified = excluded.verified,
                last_checked_at = excluded.last_checked_at
            """,
            (
                str(discord_id),
                minecraft_uuid,
                minecraft_name,
                int(verified),
                timestamp,
                timestamp,
            ),
        )
        self._connection.commit()

    def touch_check(self, discord_id: int, checked_at: Optional[int] = None) -> None:
        """Record that a check happened without changing cached verified state."""

        timestamp = checked_at or utc_now_ms()
        self._connection.execute(
            """
            INSERT INTO verifications (
                discord_id,
                verified,
                first_seen_at,
                last_checked_at
            )
            VALUES (?, 0, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                last_checked_at = excluded.last_checked_at
            """,
            (
                str(discord_id),
                timestamp,
                timestamp,
            ),
        )
        self._connection.commit()

    def get_verified_record(self, discord_id: int) -> Optional[VerificationRecord]:
        """Return a cached record only when the user is marked verified."""

        row = self._connection.execute(
            """
            SELECT
                discord_id,
                minecraft_uuid,
                minecraft_name,
                verified,
                first_seen_at,
                last_checked_at
            FROM verifications
            WHERE discord_id = ?
            """,
            (str(discord_id),),
        ).fetchone()
        if row is None or not bool(row["verified"]):
            return None
        return VerificationRecord(
            discord_id=row["discord_id"],
            minecraft_uuid=row["minecraft_uuid"],
            minecraft_name=row["minecraft_name"],
            verified=bool(row["verified"]),
            first_seen_at=row["first_seen_at"],
            last_checked_at=row["last_checked_at"],
        )

    def close(self) -> None:
        """Close the SQLite connection during bot shutdown."""

        self._connection.close()
