import hashlib
import math
import time
from pathlib import Path
from typing import Any

from app.config import settings

try:
    import lancedb  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    lancedb = None

DIMENSION = 256


def _memory_dir() -> Path:
    data_dir = Path(settings.data_dir) / "lancedb"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSION
    normalized = text.lower().strip()
    if not normalized:
        return vector

    tokens = [normalized[i : i + 2] for i in range(max(len(normalized) - 1, 1))]
    if len(normalized) == 1:
        tokens = [normalized]

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSION
        vector[index] += 1.0

    length = math.sqrt(sum(item * item for item in vector))
    if not length:
        return vector
    return [item / length for item in vector]


def _table():
    if lancedb is None:
        return None
    db = lancedb.connect(str(_memory_dir()))
    try:
        return db.open_table("memories")
    except Exception:
        return db.create_table(
            "memories",
            data=[
                {
                    "vector": _embed("初始化"),
                    "text": "初始化记忆表",
                    "kind": "system",
                    "metadata": "{}",
                    "created_at": time.time(),
                }
            ],
        )


def remember_long_term(text: str, *, kind: str = "chat", metadata: str = "{}") -> None:
    text = text.strip()
    if not text or lancedb is None:
        return
    table = _table()
    if table is None:
        return
    table.add(
        [
            {
                "vector": _embed(text),
                "text": text[:2000],
                "kind": kind,
                "metadata": metadata[:1000],
                "created_at": time.time(),
            }
        ]
    )


def search_long_term(query: str, *, limit: int = 5) -> list[str]:
    if not query.strip() or lancedb is None:
        return []
    table = _table()
    if table is None:
        return []
    try:
        rows: list[dict[str, Any]] = table.search(_embed(query)).limit(limit).to_list()
    except Exception:
        return []

    results = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if text and text != "初始化记忆表":
            results.append(text)
    return results


def is_available() -> bool:
    return lancedb is not None
