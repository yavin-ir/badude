"""SQLite-backed message store for scraped Telegram messages."""

import sqlite3
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Message:
    msg_id: int
    channel: str
    text: str
    date: str
    views: str = ""
    scraped_at: float = field(default_factory=time.time)


class MessageStore:
    def __init__(self, db_path: str = "badude.db", retention_hours: int = 24):
        self._db_path = db_path
        self._retention_seconds = retention_hours * 3600
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                channel TEXT NOT NULL,
                msg_id INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT '',
                views TEXT NOT NULL DEFAULT '',
                scraped_at REAL NOT NULL,
                PRIMARY KEY (channel, msg_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_channel_msgid
            ON messages (channel, msg_id DESC)
        """)
        conn.commit()

    def insert_new_messages(self, channel: str, messages: list[Message]) -> int:
        """Insert only messages that don't already exist. Returns count of new messages."""
        conn = self._get_conn()
        cursor = conn.executemany(
            """
            INSERT OR IGNORE INTO messages (channel, msg_id, text, date, views, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (msg.channel, msg.msg_id, msg.text, msg.date, msg.views, msg.scraped_at)
                for msg in messages
            ],
        )
        conn.commit()
        return cursor.rowcount

    def get_max_msg_id(self, channel: str) -> int | None:
        """Get the highest msg_id stored for a channel, or None if empty."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT MAX(msg_id) as max_id FROM messages WHERE channel = ?",
            (channel,),
        ).fetchone()
        return row["max_id"] if row and row["max_id"] is not None else None

    def get_channels(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT channel, COUNT(*) as count FROM messages GROUP BY channel ORDER BY channel"
        ).fetchall()
        return [{"name": row["channel"], "count": row["count"]} for row in rows]

    def get_messages(
        self, channel: str, before: int | None = None, limit: int = 20
    ) -> list[dict]:
        conn = self._get_conn()
        if before is not None:
            rows = conn.execute(
                "SELECT msg_id, channel, text, date, views FROM messages "
                "WHERE channel = ? AND msg_id < ? ORDER BY msg_id DESC LIMIT ?",
                (channel, before, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT msg_id, channel, text, date, views FROM messages "
                "WHERE channel = ? ORDER BY msg_id DESC LIMIT ?",
                (channel, limit),
            ).fetchall()
        return [
            {
                "id": row["msg_id"],
                "channel": row["channel"],
                "text": row["text"],
                "date": row["date"],
                "views": row["views"],
            }
            for row in rows
        ]

    def cleanup_expired(self) -> int:
        conn = self._get_conn()
        cutoff = time.time() - self._retention_seconds
        cursor = conn.execute(
            "DELETE FROM messages WHERE scraped_at < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount
