"""
S7 Workflow Store -- persistent storage for workflows using SQLite.
"""

import json
import sqlite3
import uuid
from typing import Any, Optional


class WorkflowStore:
    def __init__(self, db_path: str = "s7_workflows.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    code TEXT NOT NULL,
                    author TEXT NOT NULL,
                    published INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_triggers (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_args TEXT NOT NULL,
                    trigger_code TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    def create(self, name: str, display_name: str, code: str, author: str) -> str:
        """Create a new workflow. Returns the workflow ID."""
        workflow_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO workflows (id, name, display_name, code, author)
                VALUES (?, ?, ?, ?, ?)
                """,
                (workflow_id, name, display_name, code, author),
            )
            conn.commit()
        return workflow_id

    def get_by_name(self, name: str) -> Optional[dict]:
        """Get workflow by name."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, display_name, code, author, published, created_at, updated_at FROM workflows WHERE name = ?",
                (name,),
            ).fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "code": row[3],
                "author": row[4],
                "published": bool(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
            }
        return None

    def get_by_id(self, workflow_id: str) -> Optional[dict]:
        """Get workflow by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, display_name, code, author, published, created_at, updated_at FROM workflows WHERE id = ?",
                (workflow_id,),
            ).fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "code": row[3],
                "author": row[4],
                "published": bool(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
            }
        return None

    def update(self, name: str, display_name: str = None, code: str = None) -> bool:
        """Update workflow. Returns True if updated."""
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        if code is not None:
            updates.append("code = ?")
            params.append(code)
        if not updates:
            return False
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(name)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE workflows SET {', '.join(updates)} WHERE name = ?",
                params,
            )
            conn.commit()
        return cur.rowcount > 0

    def publish(self, name: str) -> bool:
        """Publish a workflow."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE workflows SET published = 1, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (name,),
            )
            conn.commit()
        return cur.rowcount > 0

    def delete(self, name: str) -> bool:
        """Delete a workflow."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM workflows WHERE name = ?", (name,))
            conn.commit()
        return cur.rowcount > 0

    def list_all(self) -> list[dict]:
        """List all workflows."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, display_name, author, published, created_at, updated_at FROM workflows ORDER BY name"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "author": row[3],
                "published": bool(row[4]),
                "created_at": row[5],
                "updated_at": row[6],
            }
            for row in rows
        ]

    def add_trigger(self, workflow_id: str, trigger_type: str, trigger_args: list, trigger_code: str) -> str:
        """Add a trigger to a workflow. Returns trigger ID."""
        trigger_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO workflow_triggers (id, workflow_id, trigger_type, trigger_args, trigger_code)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trigger_id, workflow_id, trigger_type, json.dumps(trigger_args), trigger_code),
            )
            conn.commit()
        return trigger_id

    def get_triggers(self, workflow_id: str) -> list[dict]:
        """Get all triggers for a workflow."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, trigger_type, trigger_args, trigger_code FROM workflow_triggers WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "trigger_type": row[1],
                "trigger_args": json.loads(row[2]),
                "trigger_code": row[3],
            }
            for row in rows
        ]

    def get_triggers_by_type(self, trigger_type: str, arg1: str = None, arg2: str = None, workflow_id: str = None) -> list[dict]:
        """Get all workflows with a specific trigger type and matching args."""
        with sqlite3.connect(self.db_path) as conn:
            if workflow_id:
                rows = conn.execute(
                    """
                    SELECT wt.workflow_id, wt.id, wt.trigger_type, wt.trigger_args, wt.trigger_code, w.display_name
                    FROM workflow_triggers wt
                    JOIN workflows w ON wt.workflow_id = w.id
                    WHERE wt.trigger_type = ? AND w.published = 1 AND wt.workflow_id = ?
                    """,
                    (trigger_type, workflow_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT wt.workflow_id, wt.id, wt.trigger_type, wt.trigger_args, wt.trigger_code, w.display_name
                    FROM workflow_triggers wt
                    JOIN workflows w ON wt.workflow_id = w.id
                    WHERE wt.trigger_type = ? AND w.published = 1
                    """,
                    (trigger_type,),
                ).fetchall()
        results = []
        for row in rows:
            args = json.loads(row[3])
            match = True
            if arg1 is not None and len(args) > 0:
                match = match and args[0] == arg1
            if arg2 is not None and len(args) > 1:
                match = match and args[1] == arg2
            if match:
                results.append({
                    "workflow_id": row[0],
                    "trigger_id": row[1],
                    "trigger_type": row[2],
                    "trigger_args": args,
                    "trigger_code": row[4],
                    "display_name": row[5],
                })
        return results