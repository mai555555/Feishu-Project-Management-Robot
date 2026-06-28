import re
from typing import Any

from app.feishu_client import feishu_client
from app.services.assignee_mapping import ROLE_KEYWORDS
from app.services.task_memory_service import latest_task_table, update_recent_task_items_assignment


def _normalize_text(value: object) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    if isinstance(value, dict):
        value = " ".join(str(item) for item in value.values())
    return re.sub(r"\s+", "", str(value or "")).lower()


def _text_value(value: object) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item))
            else:
                parts.append(str(item))
        return " ".join(parts)
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value or "")


def _keywords_for_alias(alias: str) -> list[str]:
    normalized_alias = _normalize_text(alias)
    keywords = [normalized_alias]
    for role, role_keywords in ROLE_KEYWORDS.items():
        if _normalize_text(role) == normalized_alias:
            keywords.extend(_normalize_text(keyword) for keyword in role_keywords)
            break
    return [keyword for keyword in dict.fromkeys(keywords) if keyword]


def _record_matches(fields: dict[str, Any], keywords: list[str]) -> bool:
    values = [
        fields.get("任务名称", ""),
        fields.get("任务说明", ""),
        fields.get("模块", ""),
        fields.get("职责标签", ""),
        fields.get("实际负责人", ""),
        fields.get("负责人标签", ""),
        fields.get("实际分配人", ""),
        fields.get("备注", ""),
    ]
    search_text = _normalize_text(" ".join(_text_value(value) for value in values))
    return any(keyword in search_text for keyword in keywords)


async def sync_recent_task_table_assignee(
    chat_id: str | None,
    alias: str,
    display_name: str,
    *,
    project_name: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    table = latest_task_table(chat_id, project_name=project_name, project_id=project_id)
    table_scope = "current_chat"
    if not table:
        table = latest_task_table(None, project_name=project_name, project_id=project_id)
        table_scope = "global"
    if not table:
        return {"updated_count": 0, "link": "", "reason": "no_recent_table"}

    app_token = str(table["app_token"])
    table_id = str(table["table_id"])
    records = await feishu_client.list_records(app_token, table_id)
    keywords = _keywords_for_alias(alias)

    updates: list[dict[str, Any]] = []
    for record in records:
        record_id = record.get("record_id")
        fields = record.get("fields") or {}
        if record_id and isinstance(fields, dict) and _record_matches(fields, keywords):
            label_field = "职责标签" if "职责标签" in fields else "负责人标签"
            assignee_field = "实际负责人" if "实际负责人" in fields else "实际分配人"
            updates.append(
                {
                    "record_id": record_id,
                    "fields": {
                        label_field: alias,
                        assignee_field: display_name,
                    },
                }
            )

    for start in range(0, len(updates), 100):
        await feishu_client.batch_update_records(app_token, table_id, updates[start : start + 100])

    local_count = update_recent_task_items_assignment(int(table["id"]), alias, display_name)
    return {
        "updated_count": len(updates),
        "local_updated_count": local_count,
        "link": str(table.get("link") or ""),
        "app_token": app_token,
        "table_id": table_id,
        "table_scope": table_scope,
        "project_name": str(table.get("project_name") or project_name or ""),
    }
