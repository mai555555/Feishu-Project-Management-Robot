import os
import re
from typing import Any

from app.services.task_memory_service import query_latest_task_items, remember_task_table


DEFAULT_FEISHU_ORIGIN = os.getenv("FEISHU_BASE_URL", "").rstrip("/")
GENERIC_PROJECT_NAMES = {
    "这个项目",
    "当前项目",
    "项目任务",
    "项目计划",
    "项目管理",
    "生成项目",
    "新项目",
    "本项目",
}


def normalize_project_id(project_name: str | None) -> str:
    return re.sub(r"\s+", "", project_name or "").strip().lower()


def _clean_project_candidate(name: str) -> str:
    name = name.strip(" ，,。；;：:")
    prefixes = (
        "请根据这份资料生成",
        "根据这份资料生成",
        "根据这个资料生成",
        "根据这份文档生成",
        "根据这个文档生成",
        "用这份资料生成",
        "把这份资料生成",
        "帮我生成",
        "生成一个",
        "生成",
        "创建一个",
        "创建",
        "这个",
        "当前",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if name.startswith(prefix) and len(name) > len(prefix) + 1:
                name = name[len(prefix):].strip(" ，,。；;：:")
                changed = True
    return name


def extract_project_name(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    patterns = (
        r"(?:记为|作为|设为|命名为|项目名是|项目叫)\s*([A-Za-z0-9_\-\u4e00-\u9fa5]{2,30}项目)",
        r"([A-Za-z0-9_\-\u4e00-\u9fa5]{2,30}项目)\s*(?:的|里|中|下)?(?:任务|负责人|后端|前端|测试|未分配|有哪些|查询|同步|记住)",
        r"(?:根据|把|用)\s*([A-Za-z0-9_\-\u4e00-\u9fa5]{2,30}项目)\s*(?:资料|文档|需求|任务)",
        r"(?:生成|创建|拆解|整理)\s*([A-Za-z0-9_\-\u4e00-\u9fa5]{2,30}项目)",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        name = _clean_project_candidate(match.group(1))
        if name and name not in GENERIC_PROJECT_NAMES and 2 <= len(name) <= 20:
            return name
    return None


def build_base_link(origin: str | None, app_token: str, table_id: str | None = None) -> str:
    base_origin = (origin or DEFAULT_FEISHU_ORIGIN).rstrip("/")
    if not base_origin:
        return ""
    link = f"{base_origin}/base/{app_token}"
    if table_id:
        link = f"{link}?table={table_id}"
    return link


def remember_existing_task_table(
    *,
    chat_id: str | None,
    sender_open_id: str | None,
    app_token: str,
    table_id: str,
    link: str,
    project_name: str | None = None,
) -> None:
    remember_task_table(
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        app_token=app_token,
        table_id=table_id,
        link=link,
        task_count=0,
        task_created_count=0,
        task_failed_count=0,
        tasks=[],
        project_name=project_name,
        project_id=normalize_project_id(project_name),
    )


def remember_generated_task_table(
    *,
    chat_id: str | None,
    sender_open_id: str | None,
    result: dict[str, Any],
    link: str,
    project_name: str | None = None,
) -> None:
    remember_task_table(
        chat_id=chat_id,
        sender_open_id=sender_open_id,
        app_token=result["app_token"],
        table_id=result["table_id"],
        link=link,
        task_count=int(result.get("task_count", 0)),
        task_created_count=int(result.get("task_created_count", 0)),
        task_failed_count=int(result.get("task_failed_count", 0)),
        tasks=result.get("tasks") or [],
        project_name=project_name,
        project_id=normalize_project_id(project_name),
    )


def format_task_table_result(result: dict[str, Any], link: str, *, project_name: str | None = None) -> str:
    project_text = f"项目：{project_name}\n" if project_name else ""
    link_text = f"\n打开链接：{link}" if link else "\n没有识别到企业域名，暂时只能返回 app_token。"
    task_sync_text = (
        f"\n飞书任务同步：成功 {result.get('task_created_count', 0)} 个，失败 {result.get('task_failed_count', 0)} 个"
    )
    if result.get("task_failed"):
        task_sync_text += "\n部分失败任务：\n" + "\n".join(result["task_failed"])
    assigned_counts = result.get("assigned_counts") or {}
    if assigned_counts:
        assignment_lines = [f"{name}: {count} 个" for name, count in assigned_counts.items()]
        task_sync_text += "\n任务分配：\n" + "\n".join(assignment_lines)
    if result.get("unmapped_owners"):
        task_sync_text += (
            "\n这些职责标签还没有绑定负责人，飞书任务先创建为未分配：\n"
            + "、".join(result["unmapped_owners"])
            + "\n可以这样补充：活动执行负责人是 @张三"
        )

    return (
        "已根据资料生成任务表。\n"
        f"{project_text}"
        f"任务数量：{result['task_count']}\n"
        f"app_token: {result['app_token']}\n"
        f"table_id: {result['table_id']}"
        f"{link_text}"
        f"{task_sync_text}"
    )


def _format_task_lines(items: list[dict[str, str]], *, limit: int = 8) -> str:
    lines = []
    for index, item in enumerate(items[:limit], start=1):
        parts = [f"{index}. {item.get('title') or '未命名任务'}"]
        module = item.get("module") or ""
        assignee = item.get("assigned_to") or ""
        priority = item.get("priority") or ""
        if module:
            parts.append(f"模块：{module}")
        if assignee:
            parts.append(f"负责人：{assignee}")
        if priority:
            parts.append(f"优先级：{priority}")
        lines.append("；".join(parts))
    return "\n".join(lines)


def format_task_query_result(
    *,
    title: str,
    query_result: dict[str, Any],
    project_name: str | None = None,
) -> str:
    table = query_result.get("table")
    if not table:
        project_hint = f"“{project_name}”" if project_name else "最近"
        return f"我还没有找到{project_hint}任务表。你可以先生成任务表，或把已有任务表链接发给我并说“记住这张任务表为官网项目”。"

    table_project = project_name or str((table or {}).get("project_name") or "")
    items = query_result.get("items") or []
    count = int(query_result.get("count") or 0)
    link = str((table or {}).get("link") or "")
    scope_note = ""
    if query_result.get("table_scope") == "global":
        scope_note = "（当前会话没有匹配任务表，已使用全局最近任务表）"

    project_prefix = f"{table_project} - " if table_project else ""
    if not items:
        text = f"{project_prefix}{title}{scope_note}：没有查到匹配的任务。"
    else:
        text = f"{project_prefix}{title}{scope_note}：共 {count} 条。\n" + _format_task_lines(items)
        if count > len(items):
            text += f"\n还有 {count - len(items)} 条没有展开。"
    if link:
        text += f"\n打开任务表：{link}"
    return text


def query_unassigned_tasks(chat_id: str | None = None, project_name: str | None = None) -> dict[str, Any]:
    return query_latest_task_items(
        chat_id=chat_id,
        unassigned=True,
        limit=12,
        project_name=project_name,
        project_id=normalize_project_id(project_name),
    )


def query_tasks_by_assignee(chat_id: str | None, assignee_name: str, project_name: str | None = None) -> dict[str, Any]:
    return query_latest_task_items(
        chat_id=chat_id,
        assigned_to=assignee_name,
        limit=12,
        project_name=project_name,
        project_id=normalize_project_id(project_name),
    )


def query_tasks_by_alias(chat_id: str | None, alias: str, project_name: str | None = None) -> dict[str, Any]:
    return query_latest_task_items(
        chat_id=chat_id,
        alias=alias,
        limit=12,
        project_name=project_name,
        project_id=normalize_project_id(project_name),
    )
