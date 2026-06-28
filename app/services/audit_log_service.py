import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import settings


def _db_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "audit_logs.sqlite3"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_open_id TEXT,
            chat_id TEXT,
            target_open_id TEXT,
            target_name TEXT,
            details TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_open_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")
    return conn


def log_audit(
    action: str,
    *,
    actor_open_id: str | None = None,
    chat_id: str | None = None,
    target_open_id: str | None = None,
    target_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if not action:
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (
                action, actor_open_id, chat_id, target_open_id, target_name, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                actor_open_id,
                chat_id,
                target_open_id,
                target_name,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                time.time(),
            ),
        )


def recent_audit_logs(limit: int = 20) -> list[dict[str, str]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT action, actor_open_id, chat_id, target_open_id, target_name, details, created_at
            FROM audit_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "action": str(row[0] or ""),
                "actor_open_id": str(row[1] or ""),
                "chat_id": str(row[2] or ""),
                "target_open_id": str(row[3] or ""),
                "target_name": str(row[4] or ""),
                "details": str(row[5] or "{}"),
                "created_at": str(row[6] or ""),
            }
        )
    return result
