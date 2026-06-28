import re
from typing import Any

from app.feishu_client import feishu_client
from app.services.organization_service import get_member
from app.services.task_memory_service import recent_task_table_entries, update_task_item_status_or_note

STATUS_DONE = "\u5df2\u5b8c\u6210"
STATUS_DOING = "\u8fdb\u884c\u4e2d"
STATUS_TODO = "\u5f85\u5f00\u59cb"
STATUS_BLOCKED = "\u963b\u585e"
STATUS_DELAYED = "\u5ef6\u671f"


def _normalize(value: object) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    if isinstance(value, dict):
        value = " ".join(str(item) for item in value.values())
    return re.sub(r"\s+", "", str(value or "")).lower()


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


def _status_field_name(fields: dict[str, Any]) -> str:
    return "\u72b6\u6001" if "\u72b6\u6001" in fields else "\u8fdb\u5ea6"


def _note_field_name(fields: dict[str, Any]) -> str:
    return "\u5907\u6ce8" if "\u5907\u6ce8" in fields else "\u4efb\u52a1\u8bf4\u660e"


def _record_title(fields: dict[str, Any]) -> str:
    return _pick_field(fields, "\u4efb\u52a1\u540d\u79f0", "\u4efb\u52a1\u63cf\u8ff0")


def _record_assignee(fields: dict[str, Any]) -> str:
    return _pick_field(fields, "\u5b9e\u9645\u8d1f\u8d23\u4eba", "\u5b9e\u9645\u5206\u914d\u4eba", "\u4efb\u52a1\u6267\u884c\u4eba")


def _record_search_text(fields: dict[str, Any]) -> str:
    values = [
        _pick_field(fields, "\u4efb\u52a1\u540d\u79f0", "\u4efb\u52a1\u63cf\u8ff0"),
        _pick_field(fields, "\u4efb\u52a1\u8bf4\u660e", "\u4efb\u52a1\u60c5\u51b5\u603b\u7ed3"),
        _pick_field(fields, "\u6a21\u5757"),
        _pick_field(fields, "\u804c\u8d23\u6807\u7b7e", "\u8d1f\u8d23\u4eba\u6807\u7b7e"),
    ]
    return _normalize(" ".join(values))


def is_self_task_update_intent(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    update_words = (
        "\u6211\u5b8c\u6210\u4e86",
        "\u6211\u505a\u5b8c\u4e86",
        "\u5df2\u5b8c\u6210",
        "\u6539\u6210\u8fdb\u884c\u4e2d",
        "\u6539\u4e3a\u8fdb\u884c\u4e2d",
        "\u72b6\u6001\u6539\u6210",
        "\u72b6\u6001\u6539\u4e3a",
        "\u52a0\u5907\u6ce8",
        "\u6dfb\u52a0\u5907\u6ce8",
        "\u5907\u6ce8",
        "\u5361\u4f4f\u4e86",
        "\u963b\u585e\u4e86",
        "\u8981\u5ef6\u671f",
        "\u5ef6\u671f\u5230",
    )
    task_words = ("\u4efb\u52a1", "\u5f00\u53d1", "\u8054\u8c03", "UI", "ui", "\u63a5\u53e3", "\u9875\u9762", "\u6a21\u5757")
    return any(word in compact for word in update_words) and ("\u6211" in compact or any(word in compact for word in task_words))


def _extract_status(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if any(word in compact for word in ("\u5b8c\u6210\u4e86", "\u505a\u5b8c\u4e86", "\u5df2\u5b8c\u6210", "\u6539\u6210\u5b8c\u6210", "\u6539\u4e3a\u5b8c\u6210")):
        return STATUS_DONE
    if any(word in compact for word in ("\u8fdb\u884c\u4e2d", "\u5f00\u59cb\u505a", "\u6b63\u5728\u505a")):
        return STATUS_DOING
    if any(word in compact for word in ("\u5f85\u5f00\u59cb", "\u672a\u5f00\u59cb")):
        return STATUS_TODO
    if any(word in compact for word in ("\u963b\u585e", "\u5361\u4f4f")):
        return STATUS_BLOCKED
    if any(word in compact for word in ("\u5ef6\u671f", "\u63a8\u8fdf")):
        return STATUS_DELAYED
    return None


def _extract_note(text: str) -> str | None:
    patterns = (
        r"(?:\u5907\u6ce8|\u8bf4\u660e|\u8865\u5145)[:\uff1a]\s*(.+)$",
        r"(?:\u52a0\u4e00\u53e5|\u52a0\u5907\u6ce8|\u6dfb\u52a0\u5907\u6ce8)[:\uff1a]?\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            return value or None
    if "\u5361\u4f4f" in text or "\u963b\u585e" in text:
        return text.strip()
    if "\u5ef6\u671f" in text:
        return text.strip()
    return None


def _extract_task_keyword(text: str, status: str | None, note: str | None) -> str:
    value = text
    if note:
        value = value.replace(note, " ")
    replacements = (
        "\u6211\u7684", "\u6211", "\u628a", "\u8bf7", "\u5e2e\u6211", "\u8fd9\u4e2a\u4efb\u52a1", "\u4efb\u52a1",
        "\u72b6\u6001", "\u6539\u6210", "\u6539\u4e3a", "\u66f4\u65b0\u4e3a", "\u8bbe\u4e3a", "\u6807\u8bb0\u4e3a",
        "\u5b8c\u6210\u4e86", "\u505a\u5b8c\u4e86", "\u5df2\u5b8c\u6210", "\u5b8c\u6210", "\u8fdb\u884c\u4e2d", "\u5f85\u5f00\u59cb",
        "\u963b\u585e\u4e86", "\u963b\u585e", "\u5361\u4f4f\u4e86", "\u5361\u4f4f", "\u5ef6\u671f", "\u52a0\u5907\u6ce8", "\u6dfb\u52a0\u5907\u6ce8", "\u5907\u6ce8", "\u8bf4\u660e", ":", "\uff1a",
    )
    for item in replacements:
        value = value.replace(item, " ")
    return re.sub(r"\s+", "", value).strip("\uff0c,\u3002.\uff1b;\uff01!")


def _owned_record(fields: dict[str, Any], display_name: str) -> bool:
    assignee = _record_assignee(fields)
    if not assignee:
        return False
    return _normalize(display_name) in _normalize(assignee) or _normalize(assignee) in _normalize(display_name)


async def update_my_task_from_text(text: str, *, sender_open_id: str | None, chat_id: str | None) -> str:
    member = get_member(sender_open_id)
    display_name = str((member or {}).get("display_name") or "").strip()
    if not display_name:
        return "\u6211\u8fd8\u4e0d\u77e5\u9053\u4f60\u5728\u516c\u53f8\u901a\u8baf\u5f55\u91cc\u7684\u540d\u5b57\uff0c\u6682\u65f6\u4e0d\u80fd\u5b89\u5168\u5730\u5224\u65ad\u54ea\u4e9b\u4efb\u52a1\u662f\u4f60\u7684\u3002\u53ef\u4ee5\u8ba9\u7ba1\u7406\u5458\u5148\u540c\u6b65\u4e00\u4e0b\u516c\u53f8\u901a\u8baf\u5f55\u3002"

    status = _extract_status(text)
    note = _extract_note(text)
    if not status and not note:
        return "\u4f60\u60f3\u6539\u8fd9\u4e2a\u4efb\u52a1\u7684\u72b6\u6001\u8fd8\u662f\u5907\u6ce8\u5462\uff1f\u53ef\u4ee5\u8fd9\u6837\u8bf4\uff1a\u201c\u6211\u5b8c\u6210\u4e86\u9996\u9875 UI\u201d\u6216\u201c\u7ed9\u63a5\u53e3\u8054\u8c03\u52a0\u5907\u6ce8\uff1a\u7b49\u540e\u7aef\u786e\u8ba4\u201d\u3002"

    keyword = _extract_task_keyword(text, status, note)
    normalized_keyword = _normalize(keyword)
    tables = recent_task_table_entries(chat_id, limit=15) + recent_task_table_entries(None, limit=15)
    seen: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []
    read_errors: list[str] = []

    for table in tables:
        key = (str(table.get("app_token") or ""), str(table.get("table_id") or ""))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        try:
            records = await feishu_client.list_records(key[0], key[1], page_size=100)
        except Exception as exc:
            read_errors.append(str(exc))
            continue
        owned = []
        for record in records:
            record_id = record.get("record_id")
            fields = record.get("fields") or {}
            if not record_id or not isinstance(fields, dict):
                continue
            if not _owned_record(fields, display_name):
                continue
            if normalized_keyword and normalized_keyword not in _record_search_text(fields):
                continue
            owned.append({"table": table, "record_id": record_id, "fields": fields})
        candidates.extend(owned)
        if candidates:
            break

    if not candidates:
        if keyword:
            return f"\u6211\u5728\u6700\u8fd1\u7684\u5386\u53f2\u4efb\u52a1\u8868\u91cc\u6ca1\u627e\u5230\u5c5e\u4e8e\u4f60\u3001\u4e14\u540d\u79f0\u5305\u542b\u201c{keyword}\u201d\u7684\u4efb\u52a1\u3002\u4f60\u53ef\u4ee5\u628a\u4efb\u52a1\u540d\u8bf4\u5f97\u518d\u5b8c\u6574\u4e00\u70b9\u3002"
        return "\u6211\u627e\u5230\u4e86\u5386\u53f2\u4efb\u52a1\u8868\uff0c\u4f46\u6ca1\u786e\u5b9a\u4f60\u8981\u6539\u54ea\u4e00\u6761\u3002\u4f60\u53ef\u4ee5\u8bf4\u5177\u4f53\u4e00\u70b9\uff0c\u6bd4\u5982\u201c\u6211\u5b8c\u6210\u4e86\u9996\u9875 UI \u5f00\u53d1\u201d\u3002"

    if len(candidates) > 1:
        names = "\u3001".join(_record_title(item["fields"]) or "\u672a\u547d\u540d\u4efb\u52a1" for item in candidates[:5])
        return f"\u6211\u627e\u5230\u4e86 {len(candidates)} \u6761\u53ef\u80fd\u662f\u4f60\u7684\u4efb\u52a1\uff1a{names}\u3002\u4f60\u518d\u628a\u4efb\u52a1\u540d\u8bf4\u5177\u4f53\u4e00\u70b9\uff0c\u6211\u5c31\u4e0d\u4f1a\u6539\u9519\u3002"

    item = candidates[0]
    fields = item["fields"]
    update_fields: dict[str, Any] = {}
    if status:
        update_fields[_status_field_name(fields)] = status
    if note:
        note_field = _note_field_name(fields)
        old_note = _field_text(fields.get(note_field))
        update_fields[note_field] = f"{old_note}\n{note}".strip() if old_note else note

    table = item["table"]
    await feishu_client.batch_update_records(
        str(table.get("app_token") or ""),
        str(table.get("table_id") or ""),
        [{"record_id": item["record_id"], "fields": update_fields}],
    )
    update_task_item_status_or_note(
        task_table_id=int(table.get("id") or 0),
        assigned_to=display_name,
        title_keyword=keyword,
        status=status,
        note=note,
    )

    task_title = _record_title(fields) or keyword or "\u8fd9\u6761\u4efb\u52a1"
    changed = []
    if status:
        changed.append(f"\u72b6\u6001\u6539\u6210\u4e86\u201c{status}\u201d")
    if note:
        changed.append("\u5907\u6ce8\u5df2\u8865\u4e0a")
    link = str(table.get("link") or "")
    link_text = f"\n\u4efb\u52a1\u8868\u5728\u8fd9\u91cc\uff1a{link}" if link else ""
    changed_text = "\u3001".join(changed)
    return f"\u597d\u7684\uff0c\u6211\u5df2\u7ecf\u628a\u4f60\u7684\u201c{task_title}\u201d{changed_text}\u4e86\u3002{link_text}"
