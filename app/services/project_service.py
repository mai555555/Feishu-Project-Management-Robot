from typing import Any

from app.feishu_client import feishu_client
from app.services.ai_client import ask_ai, parse_json_from_text
from app.services.assignee_mapping import resolve_task_assignee


TASK_FIELDS = {
    "任务名称": "title",
    "任务说明": "description",
    "模块": "module",
    "负责人": "owner",
    "优先级": "priority",
    "状态": "status",
    "开始时间": "start_date",
    "截止时间": "due_date",
    "风险": "risk",
    "依赖项": "dependencies",
    "备注": "notes",
}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(_stringify(item) for item in value if _stringify(item))
    return str(value).strip()


def _normalize_tasks(data: Any) -> list[dict[str, str]]:
    if isinstance(data, dict):
        raw_tasks = data.get("tasks") or data.get("任务") or data.get("任务列表") or []
    else:
        raw_tasks = data

    if not isinstance(raw_tasks, list):
        return []

    tasks: list[dict[str, str]] = []
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue

        tasks.append(
            {
                "title": _stringify(item.get("title") or item.get("任务名称") or f"任务 {index}"),
                "description": _stringify(item.get("description") or item.get("任务说明")),
                "module": _stringify(item.get("module") or item.get("模块")),
                "owner": _stringify(item.get("owner") or item.get("负责人") or "待定"),
                "priority": _stringify(item.get("priority") or item.get("优先级") or "中"),
                "status": _stringify(item.get("status") or item.get("状态") or "未开始"),
                "start_date": _stringify(item.get("start_date") or item.get("开始时间")),
                "due_date": _stringify(item.get("due_date") or item.get("截止时间")),
                "risk": _stringify(item.get("risk") or item.get("风险")),
                "dependencies": _stringify(item.get("dependencies") or item.get("依赖项")),
                "notes": _stringify(item.get("notes") or item.get("备注")),
            }
        )

    return tasks[:100]


def _tasks_with_assignment_labels(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    labeled_tasks: list[dict[str, str]] = []
    for task in tasks:
        labeled_task = dict(task)
        _, matched_alias = resolve_task_assignee(labeled_task)
        if matched_alias:
            labeled_task["owner"] = matched_alias
        labeled_tasks.append(labeled_task)
    return labeled_tasks


async def extract_tasks_from_document(content: str) -> list[dict[str, str]]:
    prompt = f"""
你是项目经理。请根据下面的项目文档拆解项目任务。

只返回 JSON，不要返回 Markdown，不要解释。
JSON 格式如下：
{{
  "tasks": [
    {{
      "title": "任务名称",
      "description": "任务说明",
      "module": "所属模块",
      "owner": "负责人，不确定填待定",
      "priority": "高/中/低",
      "status": "未开始",
      "start_date": "开始时间，不确定留空",
      "due_date": "截止时间，不确定留空",
      "risk": "风险点，没有留空",
      "dependencies": ["依赖项"],
      "notes": "备注"
    }}
  ]
}}

要求：
1. 拆出 5 到 30 个可执行任务；
2. 任务名称要短，任务说明要清楚；
3. 如果文档没有负责人或日期，不要编造，填待定或留空；
4. 优先级只用 高/中/低。

项目文档：
{content[:12000]}
"""
    answer = await ask_ai(prompt)
    data = parse_json_from_text(answer)
    return _normalize_tasks(data)


async def create_task_table_from_doc(
    project_name: str,
    document_content: str,
) -> dict[str, Any]:
    tasks = await extract_tasks_from_document(document_content)
    if not tasks:
        raise RuntimeError("AI 没有从文档中拆解出任务，请检查文档内容是否包含项目需求或任务信息。")

    app = await feishu_client.create_bitable_app(f"{project_name} 任务表")
    app_token = app.get("data", {}).get("app", {}).get("app_token")
    if not app_token:
        raise RuntimeError(f"多维表格已创建，但未拿到 app_token：{app}")

    table = await feishu_client.create_project_table(app_token)
    table_id = (
        table.get("data", {}).get("table", {}).get("table_id")
        or table.get("data", {}).get("table_id")
    )
    if not table_id:
        raise RuntimeError(f"任务表已创建，但未拿到 table_id：{table}")

    table_tasks = _tasks_with_assignment_labels(tasks)

    records = []
    for task in table_tasks:
        fields = {field_name: task.get(key, "") for field_name, key in TASK_FIELDS.items()}
        records.append({"fields": fields})

    await feishu_client.batch_create_records(app_token, table_id, records)
    task_sync = await create_feishu_tasks_from_tasks(
        table_tasks,
    )
    return {
        "app_token": app_token,
        "table_id": table_id,
        "task_count": len(records),
        "task_created_count": task_sync["created_count"],
        "task_failed_count": task_sync["failed_count"],
        "task_failed": task_sync["failed"],
        "assigned_counts": task_sync["assigned_counts"],
        "unmapped_owners": task_sync["unmapped_owners"],
    }


async def create_feishu_tasks_from_doc(
    document_content: str,
) -> dict[str, Any]:
    tasks = await extract_tasks_from_document(document_content)
    if not tasks:
        raise RuntimeError("AI 没有从文档中拆解出任务，请检查文档内容是否包含项目需求或任务信息。")

    return await create_feishu_tasks_from_tasks(tasks)


async def create_feishu_tasks_from_tasks(
    tasks: list[dict[str, str]],
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    failed: list[str] = []
    assigned_counts: dict[str, int] = {}
    unmapped_owners: set[str] = set()

    for task in tasks[:30]:
        title = task.get("title") or "未命名任务"
        owner = task.get("owner") or ""
        assignee_open_id, matched_alias = resolve_task_assignee(task)
        assignment_label = matched_alias or owner.strip() or "待分配"
        if not assignee_open_id:
            if owner.strip():
                unmapped_owners.add(owner.strip())
            assignment_label = "未分配"

        description_parts = [
            task.get("description", ""),
            f"模块：{task.get('module', '')}" if task.get("module") else "",
            f"负责人：{task.get('owner', '')}" if task.get("owner") else "",
            f"优先级：{task.get('priority', '')}" if task.get("priority") else "",
            f"截止时间：{task.get('due_date', '')}" if task.get("due_date") else "",
            f"风险：{task.get('risk', '')}" if task.get("risk") else "",
            f"依赖项：{task.get('dependencies', '')}" if task.get("dependencies") else "",
            f"备注：{task.get('notes', '')}" if task.get("notes") else "",
        ]
        description = "\n".join(part for part in description_parts if part)

        try:
            result = await feishu_client.create_task(
                title[:3000],
                description=description[:3000],
                assignee_open_id=assignee_open_id,
            )
            created.append(result)
            assigned_counts[assignment_label] = assigned_counts.get(assignment_label, 0) + 1
        except Exception as exc:
            failed.append(f"{title}: {exc}")

    return {
        "created_count": len(created),
        "failed_count": len(failed),
        "failed": failed[:5],
        "assigned_counts": assigned_counts,
        "unmapped_owners": sorted(unmapped_owners)[:10],
    }
