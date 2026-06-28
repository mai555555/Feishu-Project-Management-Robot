import json
import time
from pathlib import Path
from typing import Any

from app.config import settings


RECENT_FILE_TTL_SECONDS = 10 * 60


def _store_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "recent_files.json"


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
        if now - float(item.get("created_at", 0)) <= RECENT_FILE_TTL_SECONDS
    }
    _store_path().write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def remember_recent_file(
    chat_id: str | None,
    sender_open_id: str | None,
    message_id: str | None,
    file_info: dict[str, str],
) -> None:
    data = _load()
    item = {
        "created_at": time.time(),
        "message_id": message_id,
        "file_info": file_info,
    }
    data[_key(chat_id, sender_open_id)] = item
    if chat_id:
        data[_key(chat_id, None)] = item
    _save(data)


def get_recent_file(
    chat_id: str | None,
    sender_open_id: str | None,
) -> tuple[str | None, dict[str, str] | None]:
    data = _load()
    item = data.get(_key(chat_id, sender_open_id)) or data.get(_key(chat_id, None))
    _save(data)
    if not item:
        return None, None
    if time.time() - float(item.get("created_at", 0)) > RECENT_FILE_TTL_SECONDS:
        return None, None

    file_info = item.get("file_info")
    if not isinstance(file_info, dict):
        return None, None
    return str(item.get("message_id") or ""), {str(k): str(v) for k, v in file_info.items()}
