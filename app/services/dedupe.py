import sqlite3
import time
from pathlib import Path
from threading import Lock

from app.config import settings


_lock = Lock()
_db_path = Path(settings.data_dir) / "bot.sqlite3"


def _connect() -> sqlite3.Connection:
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_events (
            event_key TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL
        )
        """
    )
    return conn


def mark_processed(event_key: str) -> bool:
    if not event_key:
        return True

    now = int(time.time())
    cutoff = now - 7 * 24 * 60 * 60

    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM processed_events WHERE created_at < ?", (cutoff,))
            cursor = conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_key, created_at) VALUES (?, ?)",
                (event_key, now),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()
