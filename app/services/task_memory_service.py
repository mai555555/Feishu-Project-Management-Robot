import re
import sqlite3
import time
from pathlib import Path

from app.config import settings
from app.services.assignee_mapping import ROLE_KEYWORDS


def _db_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "task_memory.sqlite3"


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            sender_open_id TEXT,
            project_name TEXT,
            project_id TEXT,
            app_token TEXT NOT NULL,
            table_id TEXT NOT NULL,
            link TEXT,
            task_count INTEGER DEFAULT 0,
            task_created_count INTEGER DEFAULT 0,
            task_failed_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_table_id INTEGER NOT NULL,
            chat_id TEXT,
            project_name TEXT,
            project_id TEXT,
            title TEXT,
            description TEXT,
            module TEXT,
            owner_label TEXT,
            assigned_to TEXT,
            priority TEXT,
            status TEXT,
            due_date TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY(task_table_id) REFERENCES task_tables(id)
        )
        """
    )
    _ensure_column(conn, "task_tables", "project_name", "TEXT")
    _ensure_column(conn, "task_tables", "project_id", "TEXT")
    _ensure_column(conn, "task_items", "project_name", "TEXT")
    _ensure_column(conn, "task_items", "project_id", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_tables_chat_project ON task_tables(chat_id, project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_items_table ON task_items(task_table_id)")
    return conn


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _normalize_project_id(value: str | None) -> str:
    return _normalize_text(value)


def _keywords_for_alias(alias: str) -> list[str]:
    normalized_alias = _normalize_text(alias)
    keywords: list[str] = [normalized_alias] if normalized_alias else []
    for role, role_keywords in ROLE_KEYWORDS.items():
        if _normalize_text(role) == normalized_alias:
            keywords.extend(_normalize_text(keyword) for keyword in role_keywords)
            break
    return [keyword for keyword in dict.fromkeys(keywords) if keyword]


def remember_task_table(
    *,
    chat_id: str | None,
    sender_open_id: str | None,
    app_token: str,
    table_id: str,
    link: str,
    task_count: int,
    task_created_count: int,
    task_failed_count: int,
    tasks: list[dict[str, str]] | None = None,
    project_name: str | None = None,
    project_id: str | None = None,
) -> None:
    created_at = time.time()
    project_name = (project_name or "").strip() or None
    project_id = (project_id or _normalize_project_id(project_name)).strip() or None
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO task_tables (
                chat_id, sender_open_id, project_name, project_id, app_token, table_id, link, task_count,
                task_created_count, task_failed_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                sender_open_id,
                project_name,
                project_id,
                app_token,
                table_id,
                link,
                task_count,
                task_created_count,
                task_failed_count,
                created_at,
            ),
        )
        task_table_id = int(cursor.lastrowid)
        if tasks:
            conn.executemany(
                """
                INSERT INTO task_items (
                    task_table_id, chat_id, project_name, project_id, title, description, module, owner_label,
                    assigned_to, priority, status, due_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task_table_id,
                        chat_id,
                        str(task.get("project_name") or project_name or ""),
                        str(task.get("project_id") or project_id or ""),
                        str(task.get("title") or ""),
                        str(task.get("description") or ""),
                        str(task.get("module") or ""),
                        str(task.get("owner_label") or task.get("owner") or ""),
                        str(task.get("assigned_to") or ""),
                        str(task.get("priority") or ""),
                        str(task.get("status") or ""),
                        str(task.get("due_date") or ""),
                        created_at,
                    )
                    for task in tasks[:100]
                ],
            )


def _project_filter_sql(project_name: str | None = None, project_id: str | None = None) -> tuple[str, list[object]]:
    project_id = (project_id or _normalize_project_id(project_name)).strip()
    if project_id:
        like_value = f"%{project_id}%"
        name_like = f"%{project_name.strip()}%" if project_name else like_value
        return " AND (project_id = ? OR project_id LIKE ? OR project_name LIKE ?)", [project_id, like_value, name_like]
    return "", []


def recent_task_tables(chat_id: str | None = None, *, limit: int = 5) -> list[str]:
    query = """
        SELECT project_name, app_token, table_id, link, task_count, task_created_count, task_failed_count, created_at
        FROM task_tables
    """
    params: list[object] = []
    if chat_id:
        query += " WHERE chat_id = ?"
        params.append(chat_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    result = []
    for project_name, app_token, table_id, link, task_count, created_count, failed_count, _ in rows:
        project_text = f"项目={project_name}, " if project_name else ""
        link_text = f"，链接：{link}" if link else ""
        result.append(
            f"任务表：{project_text}app_token={app_token}, table_id={table_id}, 任务数={task_count}, "
            f"飞书任务成功={created_count}, 失败={failed_count}{link_text}"
        )
    return result


def _latest_task_table_id(chat_id: str | None = None, project_name: str | None = None, project_id: str | None = None) -> int | None:
    row = _latest_task_table_row(chat_id, project_name=project_name, project_id=project_id)
    return int(row[0]) if row else None


def recent_task_scope_for_alias(
    alias: str,
    chat_id: str | None = None,
    *,
    limit: int = 8,
    project_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    keywords = _keywords_for_alias(alias)
    if not keywords:
        return {"count": 0, "modules": [], "titles": [], "assigned_to": []}

    task_table_id = _latest_task_table_id(chat_id, project_name=project_name, project_id=project_id)
    if not task_table_id:
        task_table_id = _latest_task_table_id(None, project_name=project_name, project_id=project_id)
    if not task_table_id:
        return {"count": 0, "modules": [], "titles": [], "assigned_to": []}

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT title, module, owner_label, assigned_to, description
            FROM task_items
            WHERE task_table_id = ?
            ORDER BY id ASC
            """,
            (task_table_id,),
        ).fetchall()

    matched = []
    for title, module, owner_label, assigned_to, description in rows:
        search_text = _normalize_text(" ".join(str(item or "") for item in (title, module, owner_label, assigned_to, description)))
        if any(keyword in search_text for keyword in keywords):
            matched.append((title or "", module or "", owner_label or "", assigned_to or ""))

    modules = []
    titles = []
    assigned_to_values = []
    for title, module, _, assigned_to in matched:
        if module and module not in modules:
            modules.append(module)
        if title and title not in titles:
            titles.append(title)
        if assigned_to and assigned_to not in assigned_to_values:
            assigned_to_values.append(assigned_to)

    return {
        "count": len(matched),
        "modules": modules[:limit],
        "titles": titles[:limit],
        "assigned_to": assigned_to_values[:limit],
    }


def _latest_task_table_row(
    chat_id: str | None = None,
    *,
    project_name: str | None = None,
    project_id: str | None = None,
) -> tuple[int, str, str, str, int, str, str, str] | None:
    query = """
        SELECT id, app_token, table_id, link, task_count, chat_id, project_name, project_id
        FROM task_tables
        WHERE 1 = 1
    """
    params: list[object] = []
    if chat_id:
        query += " AND chat_id = ?"
        params.append(chat_id)
    project_sql, project_params = _project_filter_sql(project_name, project_id)
    query += project_sql
    params.extend(project_params)
    query += " ORDER BY created_at DESC LIMIT 1"

    with _connect() as conn:
        return conn.execute(query, params).fetchone()


def latest_task_table(
    chat_id: str | None = None,
    *,
    project_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, object] | None:
    row = _latest_task_table_row(chat_id, project_name=project_name, project_id=project_id)
    if not row:
        return None
    task_table_id, app_token, table_id, link, task_count, table_chat_id, table_project_name, table_project_id = row
    return {
        "id": int(task_table_id),
        "app_token": str(app_token),
        "table_id": str(table_id),
        "link": str(link or ""),
        "task_count": int(task_count or 0),
        "chat_id": str(table_chat_id or ""),
        "project_name": str(table_project_name or ""),
        "project_id": str(table_project_id or ""),
    }


def update_recent_task_items_assignment(
    task_table_id: int,
    alias: str,
    display_name: str,
) -> int:
    keywords = _keywords_for_alias(alias)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, module, owner_label, assigned_to, description
            FROM task_items
            WHERE task_table_id = ?
            """,
            (task_table_id,),
        ).fetchall()
        ids: list[int] = []
        for item_id, title, module, owner_label, assigned_to, description in rows:
            search_text = _normalize_text(" ".join(str(item or "") for item in (title, module, owner_label, assigned_to, description)))
            if any(keyword in search_text for keyword in keywords):
                ids.append(int(item_id))
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE task_items SET owner_label = ?, assigned_to = ? WHERE id IN ({placeholders})",
                [alias, display_name, *ids],
            )
        return len(ids)


def query_latest_task_items(
    *,
    chat_id: str | None = None,
    assigned_to: str | None = None,
    alias: str | None = None,
    unassigned: bool = False,
    limit: int = 20,
    project_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    row = _latest_task_table_row(chat_id, project_name=project_name, project_id=project_id)
    table_scope = "current_chat"
    if not row:
        row = _latest_task_table_row(None, project_name=project_name, project_id=project_id)
        table_scope = "global"
    if not row:
        return {"table": None, "items": [], "count": 0, "table_scope": table_scope}

    task_table_id, app_token, table_id, link, task_count, table_chat_id, table_project_name, table_project_id = row
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT title, description, module, owner_label, assigned_to, priority, status, due_date
            FROM task_items
            WHERE task_table_id = ?
            ORDER BY id ASC
            """,
            (task_table_id,),
        ).fetchall()

    normalized_assigned_to = _normalize_text(assigned_to)
    alias_keywords = _keywords_for_alias(alias or "")

    items: list[dict[str, str]] = []
    for title, description, module, owner_label, assignee, priority, status, due_date in rows:
        task = {
            "title": str(title or ""),
            "description": str(description or ""),
            "module": str(module or ""),
            "owner_label": str(owner_label or ""),
            "assigned_to": str(assignee or ""),
            "priority": str(priority or ""),
            "status": str(status or ""),
            "due_date": str(due_date or ""),
        }
        search_text = _normalize_text(" ".join(task.values()))

        if unassigned:
            assignee_text = _normalize_text(task["assigned_to"])
            owner_text = _normalize_text(task["owner_label"])
            if assignee_text not in {"", "未分配", "待定"} and owner_text not in {"", "待定"}:
                continue
        if normalized_assigned_to and normalized_assigned_to not in _normalize_text(task["assigned_to"]):
            continue
        if alias_keywords and not any(keyword in search_text for keyword in alias_keywords):
            continue
        items.append(task)

    table = {
        "id": int(task_table_id),
        "app_token": str(app_token),
        "table_id": str(table_id),
        "link": str(link or ""),
        "task_count": int(task_count or 0),
        "chat_id": str(table_chat_id or ""),
        "project_name": str(table_project_name or ""),
        "project_id": str(table_project_id or ""),
    }
    return {"table": table, "items": items[:limit], "count": len(items), "table_scope": table_scope}


def recent_task_table_entries(chat_id: str | None = None, *, limit: int = 10) -> list[dict[str, object]]:
    query = """
        SELECT id, app_token, table_id, link, task_count, chat_id, project_name, project_id, created_at
        FROM task_tables
    """
    params: list[object] = []
    if chat_id:
        query += " WHERE chat_id = ?"
        params.append(chat_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": int(row[0]),
            "app_token": str(row[1]),
            "table_id": str(row[2]),
            "link": str(row[3] or ""),
            "task_count": int(row[4] or 0),
            "chat_id": str(row[5] or ""),
            "project_name": str(row[6] or ""),
            "project_id": str(row[7] or ""),
            "created_at": float(row[8] or 0),
        }
        for row in rows
    ]


def update_task_item_status_or_note(
    *,
    task_table_id: int,
    assigned_to: str,
    title_keyword: str,
    status: str | None = None,
    note: str | None = None,
) -> int:
    normalized_assignee = _normalize_text(assigned_to)
    normalized_keyword = _normalize_text(title_keyword)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, module, owner_label, assigned_to
            FROM task_items
            WHERE task_table_id = ?
            """,
            (task_table_id,),
        ).fetchall()
        matched_ids: list[int] = []
        for item_id, title, description, module, owner_label, assignee in rows:
            if normalized_assignee and normalized_assignee not in _normalize_text(assignee):
                continue
            search_text = _normalize_text(" ".join(str(item or "") for item in (title, description, module, owner_label)))
            if normalized_keyword and normalized_keyword not in search_text:
                continue
            matched_ids.append(int(item_id))
        if not matched_ids:
            return 0
        assignments: list[str] = []
        params: list[object] = []
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if note is not None:
            assignments.append("description = CASE WHEN description IS NULL OR description = '' THEN ? ELSE description || '\n' || ? END")
            params.extend([note, note])
        if not assignments:
            return 0
        placeholders = ",".join("?" for _ in matched_ids)
        conn.execute(
            f"UPDATE task_items SET {', '.join(assignments)} WHERE id IN ({placeholders})",
            [*params, *matched_ids],
        )
        return len(matched_ids)

