"""
S7 Key-Value Store -- persistent storage for user data using SQLite.

Each user has their own namespace for keys.
"""

import json
import sqlite3
from typing import Any, Optional


class S7Store:
    def __init__(self, db_path: str = "s7_storage.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storage (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, key)
                )
                """
            )
            conn.commit()

    def get(self, user_id: str, key: str) -> Optional[Any]:
        """Retrieve a value for a user's key, or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM storage WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, user_id: str, key: str, value: Any) -> None:
        """Store a value for a user's key."""
        json_value = json.dumps(value)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO storage (user_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, key, json_value),
            )
            conn.commit()

    def delete(self, user_id: str, key: str) -> bool:
        """Delete a key for a user. Returns True if it existed."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM storage WHERE user_id = ? AND key = ?",
                (user_id, key),
            )
            conn.commit()
        return cur.rowcount > 0

    def list_keys(self, user_id: str) -> list[tuple[str, str]]:
        """Return all keys for a user as (key, updated_at) tuples."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT key, updated_at FROM storage WHERE user_id = ? ORDER BY key",
                (user_id,),
            ).fetchall()
        return rows

    def clear_user(self, user_id: str) -> int:
        """Delete all keys for a user. Returns count of deleted keys."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM storage WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        return cur.rowcount
