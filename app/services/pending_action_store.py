import json
import time
from pathlib import Path
from typing import Any

from app.config import settings


PENDING_ACTION_TTL_SECONDS = 10 * 60


def _store_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "pending_actions.json"


def _key(chat_id: str | None, sender_open_id: str | None) -> str:
    return f"{chat_id or '-'}:{sender_open_id or '-'}"


def _load() -> dict[str, dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    now = time.time()
    cleaned = {
        key: item
        for key, item in data.items()
        if now - float(item.get("created_at", 0)) <= PENDING_ACTION_TTL_SECONDS
    }
    _store_path().write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def remember_pending_action(
    chat_id: str | None,
    sender_open_id: str | None,
    action: str,
    payload: dict[str, Any] | None = None,
) -> None:
    data = _load()
    data[_key(chat_id, sender_open_id)] = {
        "created_at": time.time(),
        "action": action,
        "payload": payload or {},
    }
    _save(data)


def pop_pending_action(
    chat_id: str | None,
    sender_open_id: str | None,
) -> tuple[str | None, dict[str, Any]]:
    data = _load()
    key = _key(chat_id, sender_open_id)
    item = data.pop(key, None)
    _save(data)
    if not item:
        return None, {}
    if time.time() - float(item.get("created_at", 0)) > PENDING_ACTION_TTL_SECONDS:
        return None, {}
    action = item.get("action")
    payload = item.get("payload")
    return (str(action) if action else None), (payload if isinstance(payload, dict) else {})


def peek_pending_action(
    chat_id: str | None,
    sender_open_id: str | None,
) -> tuple[str | None, dict[str, Any]]:
    data = _load()
    item = data.get(_key(chat_id, sender_open_id))
    _save(data)
    if not item:
        return None, {}
    if time.time() - float(item.get("created_at", 0)) > PENDING_ACTION_TTL_SECONDS:
        return None, {}
    action = item.get("action")
    payload = item.get("payload")
    return (str(action) if action else None), (payload if isinstance(payload, dict) else {})
