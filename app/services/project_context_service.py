import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.feishu_client import feishu_client
from app.services.task_memory_service import latest_task_table, query_latest_task_items
from app.services.toolkits.task_table_tools import normalize_project_id


def _db_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "project_context.sqlite3"


def _scope_key(chat_id: str | None, sender_open_id: str | None) -> str:
    return chat_id or sender_open_id or "default"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_contexts (
            scope_key TEXT PRIMARY KEY,
            chat_id TEXT,
            sender_open_id TEXT,
            project_name TEXT,
            project_id TEXT,
            app_token TEXT,
            table_id TEXT,
            link TEXT,
            latest_file_name TEXT,
            latest_doc_title TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    return conn


def remember_project_context(
    *,
    chat_id: str | None,
    sender_open_id: str | None,
    project_name: str | None = None,
    project_id: str | None = None,
    app_token: str | None = None,
    table_id: str | None = None,
    link: str | None = None,
    latest_file_name: str | None = None,
    latest_doc_title: str | None = None,
) -> None:
    scope_key = _scope_key(chat_id, sender_open_id)
    now = time.time()
    project_name = (project_name or "").strip() or None
    project_id = (project_id or normalize_project_id(project_name)).strip() or None
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT project_name, project_id, app_token, table_id, link, latest_file_name, latest_doc_title
            FROM project_contexts
            WHERE scope_key = ?
            """,
            (scope_key,),
        ).fetchone()
        if existing:
            old_project_name, old_project_id, old_app_token, old_table_id, old_link, old_file, old_doc = existing
            project_name = project_name or old_project_name
            project_id = project_id or old_project_id
            app_token = app_token or old_app_token
            table_id = table_id or old_table_id
            link = link or old_link
            latest_file_name = latest_file_name or old_file
            latest_doc_title = latest_doc_title or old_doc
        conn.execute(
            """
            INSERT INTO project_contexts (
                scope_key, chat_id, sender_open_id, project_name, project_id, app_token, table_id,
                link, latest_file_name, latest_doc_title, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET
                chat_id = excluded.chat_id,
                sender_open_id = excluded.sender_open_id,
                project_name = excluded.project_name,
                project_id = excluded.project_id,
                app_token = excluded.app_token,
                table_id = excluded.table_id,
                link = excluded.link,
                latest_file_name = excluded.latest_file_name,
                latest_doc_title = excluded.latest_doc_title,
                updated_at = excluded.updated_at
            """,
            (
                scope_key,
                chat_id,
                sender_open_id,
                project_name,
                project_id,
                app_token,
                table_id,
                link,
                latest_file_name,
                latest_doc_title,
                now,
            ),
        )


def get_project_context(chat_id: str | None, sender_open_id: str | None = None) -> dict[str, Any] | None:
    scope_key = _scope_key(chat_id, sender_open_id)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT project_name, project_id, app_token, table_id, link, latest_file_name, latest_doc_title, updated_at
            FROM project_contexts
            WHERE scope_key = ?
            """,
            (scope_key,),
        ).fetchone()
    if not row:
        return None
    project_name, project_id, app_token, table_id, link, latest_file_name, latest_doc_title, updated_at = row
    return {
        "project_name": str(project_name or ""),
        "project_id": str(project_id or ""),
        "app_token": str(app_token or ""),
        "table_id": str(table_id or ""),
        "link": str(link or ""),
        "latest_file_name": str(latest_file_name or ""),
        "latest_doc_title": str(latest_doc_title or ""),
        "updated_at": float(updated_at or 0),
    }


def describe_project_context(chat_id: str | None, sender_open_id: str | None = None) -> str:
    context = get_project_context(chat_id, sender_open_id)
    table = None
    if context:
        table = latest_task_table(
            chat_id,
            project_name=context.get("project_name") or None,
            project_id=context.get("project_id") or None,
        )
    if not table:
        table = latest_task_table(chat_id) or latest_task_table(None)

    if not context and not table:
        return "\u6211\u8fd9\u8fb9\u8fd8\u6ca1\u6709\u8bb0\u5230\u6700\u8fd1\u7684\u9879\u76ee\u8868\u3002\u4f60\u53ef\u4ee5\u5148\u53d1\u4e00\u4efd\u6587\u6863\u6216\u6587\u4ef6\uff0c\u8ba9\u6211\u5e2e\u4f60\u751f\u6210\u4efb\u52a1\u8868\u3002"

    default_project = "\u5f53\u524d\u9879\u76ee"
    project_name = str((context or {}).get("project_name") or (table or {}).get("project_name") or default_project)
    link = str((context or {}).get("link") or (table or {}).get("link") or "")
    task_count = int((table or {}).get("task_count") or 0)

    query = query_latest_task_items(
        chat_id=chat_id,
        limit=100,
        project_name=project_name if project_name != default_project else None,
        project_id=str((context or {}).get("project_id") or "") or None,
    )
    items = query.get("items") or []
    if not task_count:
        task_count = int(query.get("count") or 0)
    unassigned_names = {"\u672a\u5206\u914d", "\u5f85\u5b9a"}
    unassigned = [
        item
        for item in items
        if not str(item.get("assigned_to") or "").strip()
        or str(item.get("assigned_to") or "").strip() in unassigned_names
    ]
    modules = []
    for item in items:
        module = str(item.get("module") or "").strip()
        if module and module not in modules:
            modules.append(module)

    lines = [f"\u73b0\u5728\u8bb0\u7740\u7684\u662f\u300c{project_name}\u300d\u3002"]
    if task_count:
        lines.append(f"\u6700\u8fd1\u8fd9\u5f20\u4efb\u52a1\u8868\u91cc\u6709 {task_count} \u4e2a\u4efb\u52a1\u3002")
    if modules:
        lines.append("\u4e3b\u8981\u6a21\u5757\u6709\uff1a" + "\u3001".join(modules[:6]) + "\u3002")
    if items and unassigned:
        lines.append(f"\u8fd8\u6709 {len(unassigned)} \u4e2a\u4efb\u52a1\u6ca1\u5206\u5230\u5177\u4f53\u8d1f\u8d23\u4eba\u3002")
    if link:
        lines.append(f"\u4efb\u52a1\u8868\u5728\u8fd9\u91cc\uff1a{link}")
    else:
        lines.append("\u4e0d\u8fc7\u8fd9\u6761\u8bb0\u5f55\u91cc\u6682\u65f6\u6ca1\u6709\u53ef\u6253\u5f00\u7684\u8868\u683c\u94fe\u63a5\u3002")
    return "\n".join(lines)


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "title", "link"):
            item = value.get(key)
            if item:
                return _field_text(item)
        return " ".join(_field_text(item) for item in value.values() if _field_text(item)).strip()
    if isinstance(value, list):
        return "?".join(item for item in (_field_text(item) for item in value) if item).strip()
    return str(value).strip()


def _pick_field(fields: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in fields:
            return _field_text(fields.get(name))
    return ""


def _is_blank_assignee(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"", "\u672a\u5206\u914d", "\u5f85\u5b9a", "none", "null", "-"}


def _top_counts(values: list[str], *, limit: int = 6) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        counts[item] = counts.get(item, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


async def _latest_bitable_items(table: dict[str, Any] | None) -> tuple[list[dict[str, str]], str]:
    if not table:
        return [], ""
    app_token = str(table.get("app_token") or "")
    table_id = str(table.get("table_id") or "")
    if not app_token or not table_id:
        return [], ""
    try:
        records = await feishu_client.list_records(app_token, table_id, page_size=100)
    except Exception as exc:
        return [], str(exc)

    items: list[dict[str, str]] = []
    for record in records:
        fields = record.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        title = _pick_field(fields, "\u4efb\u52a1\u540d\u79f0", "\u4efb\u52a1\u63cf\u8ff0")
        description = _pick_field(fields, "\u4efb\u52a1\u8bf4\u660e", "\u4efb\u52a1\u60c5\u51b5\u603b\u7ed3")
        module = _pick_field(fields, "\u6a21\u5757")
        owner_label = _pick_field(fields, "\u804c\u8d23\u6807\u7b7e", "\u8d1f\u8d23\u4eba\u6807\u7b7e")
        assigned_to = _pick_field(fields, "\u5b9e\u9645\u8d1f\u8d23\u4eba", "\u5b9e\u9645\u5206\u914d\u4eba", "\u4efb\u52a1\u6267\u884c\u4eba")
        priority = _pick_field(fields, "\u4f18\u5148\u7ea7", "\u91cd\u8981\u7d27\u6025\u7a0b\u5ea6")
        status = _pick_field(fields, "\u72b6\u6001", "\u8fdb\u5ea6")
        due_date = _pick_field(fields, "\u622a\u6b62\u65f6\u95f4", "\u622a\u6b62\u65e5\u671f", "\u7ed3\u675f\u65e5\u671f")
        if not any((title, description, module, owner_label, assigned_to, priority, status, due_date)):
            continue
        items.append(
            {
                "title": title,
                "description": description,
                "module": module,
                "owner_label": owner_label,
                "assigned_to": assigned_to,
                "priority": priority,
                "status": status,
                "due_date": due_date,
            }
        )
    return items, ""


def _is_high_priority(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"\u9ad8", "high", "p0", "p1", "\u7d27\u6025", "\u91cd\u8981"}


def _is_done_status(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"\u5df2\u5b8c\u6210", "\u5b8c\u6210", "done", "finished", "closed"}


def _is_risky_status(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(word in normalized for word in ("\u963b\u585e", "\u98ce\u9669", "\u5ef6\u671f", "\u5361\u4f4f", "blocked", "risk", "delay"))


def _build_risk_lines(*, total: int, items: list[dict[str, str]], unassigned_items: list[dict[str, str]], high_items: list[dict[str, str]], assignee_counts: list[tuple[str, int]]) -> list[str]:
    risks: list[str] = []
    if total and len(unassigned_items) / total >= 0.4:
        risks.append(f"\u672a\u5206\u914d\u4efb\u52a1\u5360\u6bd4\u6709\u70b9\u9ad8\uff0c\u5df2\u7ecf\u5230\u4e86 {len(unassigned_items)}/{total}\uff0c\u5efa\u8bae\u5148\u8865\u9f50\u8d1f\u8d23\u4eba\u3002")
    high_unassigned = [item for item in high_items if _is_blank_assignee(str(item.get("assigned_to") or ""))]
    if high_unassigned:
        names = "\u3001".join(str(item.get("title") or "\u672a\u547d\u540d\u4efb\u52a1") for item in high_unassigned[:4])
        risks.append(f"\u6709 {len(high_unassigned)} \u4e2a\u9ad8\u4f18\u5148\u7ea7\u4efb\u52a1\u8fd8\u6ca1\u6709\u8d1f\u8d23\u4eba\uff0c\u6bd4\u5982\uff1a{names}\u3002")
    if assignee_counts:
        top_name, top_count = assignee_counts[0]
        if total >= 6 and top_count / total >= 0.45:
            risks.append(f"{top_name} \u8eab\u4e0a\u7684\u4efb\u52a1\u504f\u591a\uff0c\u4e00\u4e2a\u4eba\u5360\u4e86 {top_count} \u4e2a\uff0c\u53ef\u4ee5\u770b\u770b\u8981\u4e0d\u8981\u62c6\u4e00\u90e8\u5206\u51fa\u6765\u3002")
    risky_status_items = [item for item in items if _is_risky_status(str(item.get("status") or ""))]
    if risky_status_items:
        names = "\u3001".join(str(item.get("title") or "\u672a\u547d\u540d\u4efb\u52a1") for item in risky_status_items[:4])
        risks.append(f"\u8868\u683c\u91cc\u6709 {len(risky_status_items)} \u4e2a\u4efb\u52a1\u72b6\u6001\u770b\u8d77\u6765\u6709\u98ce\u9669\uff1a{names}\u3002")
    return risks


async def describe_project_daily_summary(chat_id: str | None, sender_open_id: str | None = None) -> str:
    context = get_project_context(chat_id, sender_open_id)
    default_project = "\u5f53\u524d\u9879\u76ee"
    project_name = str((context or {}).get("project_name") or "").strip()
    project_id = str((context or {}).get("project_id") or "").strip()

    query = query_latest_task_items(
        chat_id=chat_id,
        limit=200,
        project_name=project_name or None,
        project_id=project_id or None,
    )
    table = query.get("table")
    if not table:
        table = latest_task_table(chat_id) or latest_task_table(None)
        if table:
            query = query_latest_task_items(chat_id=chat_id, limit=200)

    if not table:
        return "\u6211\u8fd9\u8fb9\u8fd8\u6ca1\u6709\u627e\u5230\u6700\u8fd1\u7684\u4efb\u52a1\u8868\u3002\u5148\u628a\u9879\u76ee\u8d44\u6599\u53d1\u7ed9\u6211\uff0c\u6211\u751f\u6210\u4efb\u52a1\u8868\u540e\u5c31\u80fd\u7ed9\u4f60\u505a\u9879\u76ee\u603b\u7ed3\u4e86\u3002"

    live_items, live_error = await _latest_bitable_items(table if isinstance(table, dict) else None)
    memory_items = list(query.get("items") or [])
    items = live_items or memory_items
    total = len(items) if live_items else int((table or {}).get("task_count") or query.get("count") or len(items))
    project_title = project_name or str((table or {}).get("project_name") or default_project)
    link = str((context or {}).get("link") or (table or {}).get("link") or "")

    assigned_items = [item for item in items if not _is_blank_assignee(str(item.get("assigned_to") or ""))]
    unassigned_items = [item for item in items if _is_blank_assignee(str(item.get("assigned_to") or ""))]
    high_items = [item for item in items if _is_high_priority(str(item.get("priority") or ""))]
    done_items = [item for item in items if _is_done_status(str(item.get("status") or ""))]

    module_counts = _top_counts([str(item.get("module") or "") for item in items])
    assignee_counts = _top_counts([str(item.get("assigned_to") or "") for item in assigned_items])
    owner_labels = _top_counts([str(item.get("owner_label") or "") for item in unassigned_items])

    source_text = "\u6211\u521a\u4ece\u98de\u4e66\u4efb\u52a1\u8868\u91cc\u62c9\u4e86\u6700\u65b0\u8bb0\u5f55" if live_items else "\u6211\u5148\u7528\u6700\u8fd1\u8bb0\u4f4f\u7684\u4efb\u52a1\u6570\u636e\u770b\u4e86\u4e00\u4e0b"
    lines = [f"{source_text}\uff0c\u300c{project_title}\u300d\u73b0\u5728\u5927\u6982\u662f\u8fd9\u6837\u3002"]
    lines.append(f"\u4e00\u5171 {total} \u4e2a\u4efb\u52a1\uff0c\u5df2\u7ecf\u5206\u5230\u5177\u4f53\u8d1f\u8d23\u4eba\u7684\u6709 {len(assigned_items)} \u4e2a\uff0c\u8fd8\u6709 {len(unassigned_items)} \u4e2a\u6ca1\u5206\u914d\u3002")
    if done_items:
        lines.append(f"\u72b6\u6001\u4e0a\u770b\uff0c\u5df2\u5b8c\u6210 {len(done_items)} \u4e2a\uff0c\u5269\u4e0b\u7684\u8fd8\u5728\u63a8\u8fdb\u4e2d\u3002")
    if module_counts:
        module_text = "\u3001".join(f"{name} {count}\u4e2a" for name, count in module_counts)
        lines.append(f"\u4efb\u52a1\u4e3b\u8981\u96c6\u4e2d\u5728\uff1a{module_text}\u3002")
    if assignee_counts:
        assignee_text = "\u3001".join(f"{name} {count}\u4e2a" for name, count in assignee_counts)
        lines.append(f"\u5df2\u5206\u914d\u7684\u90e8\u5206\u91cc\uff0c\u76ee\u524d\u8d1f\u8d23\u4eba\u5206\u5e03\u662f\uff1a{assignee_text}\u3002")
    if high_items:
        lines.append(f"\u9ad8\u4f18\u5148\u7ea7\u4efb\u52a1\u6709 {len(high_items)} \u4e2a\uff0c\u8fd9\u4e9b\u5efa\u8bae\u653e\u5728\u4eca\u5929\u4f18\u5148\u770b\u3002")

    risk_lines = _build_risk_lines(total=total, items=items, unassigned_items=unassigned_items, high_items=high_items, assignee_counts=assignee_counts)
    if risk_lines:
        lines.append("\u8981\u7559\u610f\u7684\u5730\u65b9\uff1a" + " ".join(risk_lines))
    elif owner_labels:
        label_text = "\u3001".join(name for name, _ in owner_labels[:5])
        lines.append(f"\u6ca1\u5206\u914d\u7684\u4efb\u52a1\u4e3b\u8981\u6d89\u53ca\uff1a{label_text}\u3002\u53ef\u4ee5\u5148\u628a\u8fd9\u51e0\u7c7b\u804c\u8d23\u7ed1\u5230\u5177\u4f53\u540c\u4e8b\u3002")
    elif unassigned_items:
        lines.append("\u672a\u5206\u914d\u7684\u4efb\u52a1\u6682\u65f6\u6ca1\u6709\u660e\u786e\u804c\u8d23\u6807\u7b7e\uff0c\u53ef\u4ee5\u5148\u6253\u5f00\u8868\u683c\u770b\u4e00\u4e0b\u8fd9\u4e9b\u4efb\u52a1\u8981\u5f52\u5230\u54ea\u4e2a\u5c97\u4f4d\u3002")
    else:
        lines.append("\u5206\u914d\u72b6\u6001\u8fd8\u4e0d\u9519\uff0c\u8fd9\u5f20\u8868\u91cc\u7684\u4efb\u52a1\u90fd\u5df2\u7ecf\u6709\u5177\u4f53\u8d1f\u8d23\u4eba\u4e86\u3002")

    if live_error:
        lines.append("\u987a\u5e26\u8bf4\u4e0b\uff0c\u8fd9\u6b21\u6ca1\u80fd\u76f4\u63a5\u8bfb\u5230\u98de\u4e66\u8868\u683c\u7684\u6700\u65b0\u8bb0\u5f55\uff0c\u6240\u4ee5\u7528\u7684\u662f\u673a\u5668\u4eba\u6700\u8fd1\u8bb0\u4f4f\u7684\u6570\u636e\u3002")
    if link:
        lines.append(f"\u4efb\u52a1\u8868\u6211\u4e5f\u653e\u8fd9\u91cc\uff1a{link}")
    return "\n".join(lines)
