from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from config import STARTING_TELEGRAM_CHANNELS
from models import AIStatus, DiscoveredChat, LeadRecord, LeadSource, RawPost

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id TEXT NOT NULL,
        source TEXT NOT NULL,
        text TEXT NOT NULL,
        author TEXT NOT NULL,
        contact TEXT,
        timestamp TEXT NOT NULL,
        ai_status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT,
        summary TEXT,
        UNIQUE(external_id, source)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(ai_status);",
    """
    CREATE TABLE IF NOT EXISTS discovered_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        keyword TEXT,
        added_at TEXT NOT NULL
    );
    """,
]

SEED_KEYWORD = "seed"


class LeadDatabase:
    """Async SQLite storage with INSERT OR IGNORE deduplication."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        for stmt in SCHEMA_STATEMENTS:
            await self._conn.execute(stmt)
        await self._conn.commit()
        await self._migrate_schema()
        await self._seed_discovered_chats_if_empty()
        logger.info("SQLite connected: %s", self._db_path)

    async def _migrate_schema(self) -> None:
        """Add inbox columns to existing databases."""
        assert self._conn is not None
        cursor = await self._conn.execute("PRAGMA table_info(leads)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "inbox_list" not in cols:
            await self._conn.execute("ALTER TABLE leads ADD COLUMN inbox_list TEXT")
        if "inbox_list_at" not in cols:
            await self._conn.execute("ALTER TABLE leads ADD COLUMN inbox_list_at TEXT")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _seed_discovered_chats_if_empty(self) -> None:
        """
        On first run, populate discovered_chats from STARTING_TELEGRAM_CHANNELS
        so tg_parser can poll immediately without waiting for auto-discovery.
        """
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM discovered_chats"
        )
        row = await cursor.fetchone()
        if row and row["cnt"] > 0:
            return

        seeded = 0
        now = datetime.now(timezone.utc).isoformat()
        for username in STARTING_TELEGRAM_CHANNELS:
            normalized = username.lstrip("@").lower()
            cur = await self._conn.execute(
                """
                INSERT OR IGNORE INTO discovered_chats (username, keyword, added_at)
                VALUES (?, ?, ?)
                """,
                (normalized, SEED_KEYWORD, now),
            )
            if cur.rowcount > 0:
                seeded += 1

        await self._conn.commit()
        logger.info(
            "Seeded %d/%d starting Telegram channel(s) into discovered_chats",
            seeded,
            len(STARTING_TELEGRAM_CHANNELS),
        )

    # ------------------------------------------------------------------
    # leads
    # ------------------------------------------------------------------

    async def lead_exists(self, external_id: str, source: LeadSource) -> bool:
        """Check duplicate by (external_id, source) BEFORE any OpenAI call."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT 1 FROM leads
            WHERE external_id = ? AND source = ?
            LIMIT 1
            """,
            (external_id, source.value),
        )
        return await cursor.fetchone() is not None

    async def insert_lead(self, post: RawPost) -> bool:
        """
        Insert a new lead. Returns True if inserted, False if duplicate.
        Call lead_exists() first for explicit early skip + logging.
        """
        if await self.lead_exists(post.external_id, post.source):
            return False

        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            INSERT OR IGNORE INTO leads
                (external_id, source, text, author, contact, timestamp, ai_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post.external_id,
                post.source.value,
                post.text,
                post.author,
                post.contact,
                post.timestamp.isoformat(),
                AIStatus.PENDING.value,
            ),
        )
        await self._conn.commit()
        inserted = cursor.rowcount > 0
        if inserted:
            logger.debug("Lead saved [%s] %s", post.source.value, post.external_id)
        return inserted

    async def update_lead_ai(
        self,
        external_id: str,
        source: LeadSource,
        status: AIStatus,
        reason: str,
        summary: Optional[str] = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE leads
            SET ai_status = ?, reason = ?, summary = ?
            WHERE external_id = ? AND source = ?
            """,
            (status.value, reason, summary, external_id, source.value),
        )
        await self._conn.commit()

    async def get_lead_id(self, external_id: str, source: LeadSource) -> Optional[int]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT id FROM leads
            WHERE external_id = ? AND source = ?
            LIMIT 1
            """,
            (external_id, source.value),
        )
        row = await cursor.fetchone()
        return int(row["id"]) if row else None

    async def set_lead_inbox_list(self, lead_id: int, list_name: str) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            UPDATE leads
            SET inbox_list = ?, inbox_list_at = ?
            WHERE id = ?
            """,
            (list_name, datetime.now(timezone.utc).isoformat(), lead_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_inbox_counts(self) -> dict[str, int]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT inbox_list, COUNT(*) AS cnt
            FROM leads
            WHERE ai_status = ? AND inbox_list IS NOT NULL
            GROUP BY inbox_list
            """,
            (AIStatus.QUALIFIED.value,),
        )
        rows = await cursor.fetchall()
        return {row["inbox_list"]: row["cnt"] for row in rows}

    async def count_qualified_leads(self) -> int:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM leads WHERE ai_status = ?",
            (AIStatus.QUALIFIED.value,),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def count_uncategorized_qualified(self) -> int:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM leads
            WHERE ai_status = ? AND inbox_list IS NULL
            """,
            (AIStatus.QUALIFIED.value,),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_qualified_leads(self, limit: int = 50) -> list[LeadRecord]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT * FROM leads
            WHERE ai_status = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (AIStatus.QUALIFIED.value, limit),
        )
        rows = await cursor.fetchall()
        return [_row_to_lead(row) for row in rows]

    # ------------------------------------------------------------------
    # discovered_chats (Telegram)
    # ------------------------------------------------------------------

    async def add_discovered_chat(self, username: str, keyword: str) -> bool:
        """INSERT OR IGNORE. Returns True when a new chat was added."""
        normalized = username.lstrip("@").lower()
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            INSERT OR IGNORE INTO discovered_chats (username, keyword, added_at)
            VALUES (?, ?, ?)
            """,
            (normalized, keyword, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()
        if cursor.rowcount > 0:
            logger.info("Discovered chat @%s (keyword: %s)", normalized, keyword)
            return True
        return False

    async def get_discovered_chats(self) -> list[DiscoveredChat]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            """
            SELECT * FROM discovered_chats
            ORDER BY
                CASE WHEN keyword = ? THEN 0 ELSE 1 END,
                added_at ASC
            """,
            (SEED_KEYWORD,),
        )
        rows = await cursor.fetchall()
        return [_row_to_chat(row) for row in rows]


def _row_to_lead(row: aiosqlite.Row) -> LeadRecord:
    return LeadRecord(
        id=row["id"],
        external_id=row["external_id"],
        source=LeadSource(row["source"]),
        text=row["text"],
        author=row["author"],
        contact=row["contact"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        ai_status=AIStatus(row["ai_status"]),
        reason=row["reason"],
        summary=row["summary"],
        inbox_list=row["inbox_list"] if "inbox_list" in row.keys() else None,
        inbox_list_at=(
            datetime.fromisoformat(row["inbox_list_at"])
            if "inbox_list_at" in row.keys() and row["inbox_list_at"]
            else None
        ),
    )


def _row_to_chat(row: aiosqlite.Row) -> DiscoveredChat:
    return DiscoveredChat(
        id=row["id"],
        username=row["username"],
        keyword=row["keyword"],
        added_at=datetime.fromisoformat(row["added_at"]),
    )
