"""
S7 Macro Store -- persistent storage for named macros using SQLite.
"""

import sqlite3
from typing import Optional


class MacroStore:
    def __init__(self, db_path: str = "s7_macros.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macros (
                    name TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def get(self, name: str) -> Optional[str]:
        """Retrieve the code for a named macro, or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT code FROM macros WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def get_with_author(self, name: str) -> Optional[tuple[str, str]]:
        """Retrieve (code, author) for a named macro, or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT code, author FROM macros WHERE name = ?", (name,)
            ).fetchone()
        return (row[0], row[1]) if row else None

    def set(self, name: str, code: str, author: str) -> None:
        """Create or update a named macro."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO macros (name, code, author, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    code = excluded.code,
                    author = excluded.author,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (name, code, author),
            )
            conn.commit()

    def remove(self, name: str) -> bool:
        """Delete a named macro. Returns True if it existed."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM macros WHERE name = ?", (name,))
            conn.commit()
        return cur.rowcount > 0

    def list_all(self) -> list[tuple[str, str, str]]:
        """Return all macros as (name, author, updated_at) tuples."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT name, author, updated_at FROM macros ORDER BY name"
            ).fetchall()
        return rows
