import json
import re
import sqlite3
import time
from pathlib import Path

from app.config import settings


ROLE_KEYWORDS = {
    "前端": ["前端", "ui", "页面", "界面", "首页", "表现层", "小程序", "组件", "样式", "交互"],
    "后端": ["后端", "接口", "服务端", "api", "数据库", "数据表", "云函数", "存储", "权限"],
    "产品": ["产品", "需求", "原型", "规划", "评审", "功能设计"],
    "测试": ["测试", "验收", "联调", "bug", "缺陷", "用例"],
    "设计": ["设计", "视觉", "交互", "ui", "原型"],
    "运营": ["运营", "资讯", "公告", "内容", "推广", "活动"],
}


def _data_dir() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _legacy_mapping_path() -> Path:
    return _data_dir() / "assignees.json"


def _db_path() -> Path:
    return _data_dir() / "assignees.sqlite3"


def _normalize_alias(alias: str) -> str:
    return re.sub(r"\s+", "", alias).strip().lower()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assignee_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL DEFAULT 'global',
            chat_id TEXT,
            project_id TEXT,
            alias TEXT NOT NULL,
            open_id TEXT NOT NULL,
            display_name TEXT,
            updated_by_open_id TEXT,
            updated_at REAL NOT NULL,
            UNIQUE(scope, chat_id, project_id, alias)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assignee_rules_alias ON assignee_rules(alias)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assignee_rules_chat ON assignee_rules(chat_id)")
    return conn


def _parse_legacy_mapping(data: object) -> dict[str, dict[str, str]]:
    if not isinstance(data, dict):
        return {}

    mapping: dict[str, dict[str, str]] = {}
    for alias, item in data.items():
        normalized_alias = _normalize_alias(str(alias))
        if not normalized_alias:
            continue
        if isinstance(item, str):
            open_id = item.strip()
            display_name = ""
        elif isinstance(item, dict):
            open_id = str(item.get("open_id") or item.get("openId") or "").strip()
            display_name = str(item.get("display_name") or item.get("displayName") or "").strip()
        else:
            continue
        if open_id:
            mapping[normalized_alias] = {"open_id": open_id, "display_name": display_name}
    return mapping


def _migrate_legacy_json() -> None:
    path = _legacy_mapping_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    mapping = _parse_legacy_mapping(data)
    if not mapping:
        return

    now = time.time()
    with _connect() as conn:
        for alias, item in mapping.items():
            conn.execute(
                """
                INSERT INTO assignee_rules (
                    scope, chat_id, project_id, alias, open_id, display_name, updated_by_open_id, updated_at
                ) VALUES ('global', NULL, NULL, ?, ?, ?, NULL, ?)
                ON CONFLICT(scope, chat_id, project_id, alias) DO UPDATE SET
                    open_id = excluded.open_id,
                    display_name = excluded.display_name,
                    updated_at = excluded.updated_at
                """,
                (alias, item.get("open_id", ""), item.get("display_name", ""), now),
            )


def _rows_to_mapping(rows: list[sqlite3.Row] | list[tuple]) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        alias, open_id, display_name = row[0], row[1], row[2]
        if alias and open_id:
            mapping[str(alias)] = {"open_id": str(open_id), "display_name": str(display_name or "")}
    return mapping


def _load_mapping(chat_id: str | None = None, project_id: str | None = None) -> dict[str, dict[str, str]]:
    _migrate_legacy_json()
    with _connect() as conn:
        mapping: dict[str, dict[str, str]] = {}
        if chat_id or project_id:
            rows = conn.execute(
                """
                SELECT alias, open_id, display_name
                FROM assignee_rules
                WHERE (scope = 'chat' AND chat_id IS ?)
                   OR (scope = 'project' AND project_id IS ?)
                ORDER BY updated_at ASC
                """,
                (chat_id, project_id),
            ).fetchall()
            mapping.update(_rows_to_mapping(rows))

        rows = conn.execute(
            """
            SELECT alias, open_id, display_name
            FROM assignee_rules
            WHERE scope = 'global'
            ORDER BY updated_at ASC
            """
        ).fetchall()
        global_mapping = _rows_to_mapping(rows)
        global_mapping.update(mapping)
        return global_mapping


def bind_assignee(
    alias: str,
    open_id: str,
    display_name: str = "",
    *,
    chat_id: str | None = None,
    project_id: str | None = None,
    updated_by_open_id: str | None = None,
) -> str:
    normalized = _normalize_alias(alias)
    if not normalized:
        raise ValueError("负责人名称不能为空")
    if not open_id:
        raise ValueError("没有拿到当前用户 open_id，请在飞书里发送绑定指令")

    scope = "project" if project_id else ("chat" if chat_id else "global")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO assignee_rules (
                scope, chat_id, project_id, alias, open_id, display_name, updated_by_open_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, chat_id, project_id, alias) DO UPDATE SET
                open_id = excluded.open_id,
                display_name = excluded.display_name,
                updated_by_open_id = excluded.updated_by_open_id,
                updated_at = excluded.updated_at
            """,
            (
                scope,
                chat_id if scope == "chat" else None,
                project_id if scope == "project" else None,
                normalized,
                open_id,
                display_name.strip(),
                updated_by_open_id,
                time.time(),
            ),
        )
    return normalized


def unbind_assignee(alias: str, *, chat_id: str | None = None, project_id: str | None = None) -> bool:
    normalized = _normalize_alias(alias)
    if not normalized:
        return False
    scope = "project" if project_id else ("chat" if chat_id else "global")
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM assignee_rules WHERE scope = ? AND chat_id IS ? AND project_id IS ? AND alias = ?",
            (scope, chat_id if scope == "chat" else None, project_id if scope == "project" else None, normalized),
        )
        return cursor.rowcount > 0


def list_assignees(chat_id: str | None = None, project_id: str | None = None) -> list[str]:
    mapping = _load_mapping(chat_id, project_id)
    result = []
    for alias in sorted(mapping.keys()):
        display_name = mapping[alias].get("display_name") or "未记录姓名"
        result.append(f"{alias} -> {display_name}")
    return result


def get_assignee_mapping(
    alias: str | None,
    *,
    chat_id: str | None = None,
    project_id: str | None = None,
) -> tuple[str, dict[str, str]] | None:
    if not alias:
        return None

    normalized = _normalize_alias(alias)
    if not normalized:
        return None

    mapping = _load_mapping(chat_id, project_id)
    if normalized in mapping:
        return normalized, mapping[normalized]

    for item_alias, item in mapping.items():
        if item_alias and item_alias in normalized:
            return item_alias, item

    for item_alias, item in mapping.items():
        if normalized and normalized in item_alias:
            return item_alias, item

    return None


def known_assignee_aliases(chat_id: str | None = None, project_id: str | None = None) -> list[str]:
    mapping = _load_mapping(chat_id, project_id)
    aliases = set(mapping.keys()) | {_normalize_alias(role) for role in ROLE_KEYWORDS.keys()}
    return sorted(alias for alias in aliases if alias)


def role_scope_text(alias: str | None) -> str:
    if not alias:
        return ""
    normalized = _normalize_alias(alias)
    for role, keywords in ROLE_KEYWORDS.items():
        if _normalize_alias(role) == normalized:
            return "、".join(keywords[:8])
    return ""


def resolve_assignee_info(
    owner: str | None,
    *,
    chat_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, str] | None:
    if not owner:
        return None

    normalized_owner = _normalize_alias(owner)
    if not normalized_owner:
        return None

    mapping = _load_mapping(chat_id, project_id)
    if normalized_owner in mapping:
        return mapping[normalized_owner]

    for alias, item in mapping.items():
        if alias and alias in normalized_owner:
            return item

    for alias, item in mapping.items():
        if normalized_owner and normalized_owner in alias:
            return item

    return None


def resolve_assignee_open_id(owner: str | None) -> str | None:
    info = resolve_assignee_info(owner)
    return info.get("open_id") if info else None


def resolve_task_assignment(task: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    chat_id = task.get("chat_id") or None
    project_id = task.get("project_id") or None
    mapping = _load_mapping(chat_id, project_id)
    owner = task.get("owner") or ""

    info = resolve_assignee_info(owner, chat_id=chat_id, project_id=project_id)
    if info:
        return info.get("open_id"), owner.strip() or None, info.get("display_name") or None

    search_text = _normalize_alias(
        " ".join(
            [
                task.get("title", ""),
                task.get("description", ""),
                task.get("module", ""),
                owner,
                task.get("notes", ""),
            ]
        )
    )

    for alias, item in mapping.items():
        if alias and alias in search_text:
            return item.get("open_id"), alias, item.get("display_name") or None

    for role, keywords in ROLE_KEYWORDS.items():
        normalized_role = _normalize_alias(role)
        if normalized_role not in mapping:
            continue
        if any(_normalize_alias(keyword) in search_text for keyword in keywords):
            item = mapping[normalized_role]
            return item.get("open_id"), normalized_role, item.get("display_name") or None

    return None, None, None


def resolve_task_assignee(task: dict[str, str]) -> tuple[str | None, str | None]:
    open_id, matched_alias, _ = resolve_task_assignment(task)
    return open_id, matched_alias
