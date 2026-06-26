import re
from dataclasses import dataclass

from app.feishu_client import feishu_client
from app.services.ai_client import ask_ai
from app.services.assignee_mapping import bind_assignee, list_assignees, unbind_assignee
from app.services.docx_service import extract_docx_text
from app.services.pdf_service import extract_pdf_text
from app.services.project_service import create_feishu_tasks_from_doc, create_task_table_from_doc
from app.services.recent_file_store import get_recent_file, remember_recent_file


DOCX_RE = re.compile(r"/docx/([A-Za-z0-9_-]{27,})")
FEISHU_FILE_RE = re.compile(r"/(?:file|drive)/(?:[^/\s]+/)?([A-Za-z0-9_-]{20,})")
FEISHU_HOST_RE = re.compile(r"https://([^/\s]+)/(?:docx|docs|base|wiki|file|drive)/", re.I)
TASK_TABLE_INTENT_KEYWORDS = (
    "生成任务表",
    "任务表",
    "拆任务",
    "拆一下任务",
    "拆解任务",
    "生成任务",
    "项目计划",
    "生成计划",
    "任务管理表",
    "项目管理表",
    "根据刚才",
    "刚才的文件",
    "刚才那个文件",
    "这个文件",
    "这个文档",
)


@dataclass(frozen=True)
class CommandResult:
    text: str


def _extract_docx_id(text: str) -> str | None:
    match = DOCX_RE.search(text)
    if match:
        return match.group(1)
    return None


def _extract_feishu_origin(text: str) -> str | None:
    match = FEISHU_HOST_RE.search(text)
    if not match:
        return None
    return f"https://{match.group(1)}"


def _base_link(origin: str | None, app_token: str, table_id: str | None = None) -> str:
    if not origin:
        return ""
    link = f"{origin}/base/{app_token}"
    if table_id:
        link = f"{link}?table={table_id}"
    return link


def _is_task_table_intent(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.startswith("/生成任务表"):
        return True
    if normalized.startswith("/"):
        return False
    return any(keyword.lower() in normalized for keyword in TASK_TABLE_INTENT_KEYWORDS)


def _find_first_key(value: object, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item:
                return str(item)
        for item in value.values():
            found = _find_first_key(item, keys)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_first_key(item, keys)
            if found:
                return found

    return None


def _file_info_from_payload(payload: object) -> dict[str, str] | None:
    file_key = _find_first_key(payload, {"file_key", "fileKey"})
    file_name = (
        _find_first_key(payload, {"file_name", "fileName", "name", "title"})
        or "attachment.pdf"
    )
    if file_key:
        return {"file_key": file_key, "file_name": file_name}

    file_token = _find_first_key(
        payload,
        {"token", "file_token", "fileToken", "obj_token", "objToken", "doc_token", "docToken"},
    )
    if file_token:
        return {"file_token": file_token, "file_name": file_name}

    url = _find_first_key(payload, {"url", "href", "link"})
    if url:
        return {"url": url, "file_name": file_name}

    return None


def _mention_open_id(mention: dict[str, object]) -> str | None:
    mention_id = mention.get("id")
    if isinstance(mention_id, dict):
        for key in ("open_id", "openId", "user_id", "userId"):
            value = mention_id.get(key)
            if value:
                return str(value)
    if isinstance(mention_id, str) and mention_id.startswith("ou_"):
        return mention_id

    for key in ("open_id", "openId", "user_id", "userId"):
        value = mention.get(key)
        if value:
            return str(value)
    return None


def _target_mention_open_id(
    mentions: list[dict[str, object]] | None,
    sender_open_id: str | None,
) -> str | None:
    candidates = [
        open_id
        for mention in mentions or []
        if (open_id := _mention_open_id(mention)) and open_id != sender_open_id
    ]
    if candidates:
        return candidates[-1]
    return None


def _help_text() -> str:
    return (
        "可用指令：\n"
        "直接发送问题，也可以和我对话\n"
        "/问 你的问题\n"
        "/读文档 飞书文档链接\n"
        "/生成项目表 项目名称\n"
        "/生成任务表 飞书文档链接\n"
        "上传文件后，也可以直接说：生成任务表 / 拆任务 / 项目计划\n"
        "/创建飞书任务 任务标题\n"
        "/生成飞书任务 飞书文档链接\n"
        "/绑定负责人 名字或岗位 @员工\n"
        "/解绑负责人 名字或岗位\n"
        "/负责人列表\n"
        "/帮助"
    )


async def _ask(prompt: str) -> CommandResult:
    if not prompt:
        return CommandResult("请发送：/问 你的问题")

    try:
        answer = await ask_ai(prompt)
    except Exception as exc:
        return CommandResult(f"AI 请求失败：{exc}")

    return CommandResult(answer[:3800])


def _doc_link_help(command: str) -> str:
    return (
        f"请发送真实的飞书新版文档链接，例如：\n"
        f"{command} https://xxx.feishu.cn/docx/真实文档Token\n\n"
        "注意：示例链接里的 xxxxx 不能直接使用。"
    )


async def _download_attached_file(message_id: str | None, file_info: dict[str, str]) -> bytes | None:
    file_key = file_info.get("file_key")
    if file_key and message_id:
        return await feishu_client.download_message_file(message_id, file_key)

    file_token = file_info.get("file_token")
    if not file_token and file_info.get("url"):
        match = FEISHU_FILE_RE.search(file_info["url"])
        if match:
            file_token = match.group(1)
    if not file_token:
        return None

    return await feishu_client.download_drive_file(file_token)


async def _read_attached_document(message_id: str | None, file_info: dict[str, str] | None) -> str | None:
    if not file_info and message_id:
        message_data = await feishu_client.get_message(message_id)
        file_info = _file_info_from_payload(message_data)

    if not file_info:
        return None

    file_name = file_info.get("file_name", "")
    lower_name = file_name.lower()
    if lower_name and "." in lower_name and not lower_name.endswith((".pdf", ".docx")):
        return None

    file_bytes = await _download_attached_file(message_id, file_info)
    if not file_bytes:
        return None

    if lower_name.endswith(".docx"):
        return extract_docx_text(file_bytes)
    return extract_pdf_text(file_bytes)


async def handle_command(
    text: str,
    *,
    sender_open_id: str | None = None,
    chat_id: str | None = None,
    mentions: list[dict[str, object]] | None = None,
    message_id: str | None = None,
    file_info: dict[str, str] | None = None,
) -> CommandResult:
    normalized = text.strip()

    if file_info and not normalized.startswith("/"):
        remember_recent_file(chat_id, sender_open_id, message_id, file_info)
        file_name = file_info.get("file_name") or "文件"
        return CommandResult(
            f"已收到文件：{file_name}\n"
            "接下来发送“生成任务表”或“拆任务”，我会读取这个文件生成项目任务表。"
        )

    if normalized in {"/帮助", "帮助", "/help"}:
        return CommandResult(_help_text())

    if normalized.startswith("/绑定负责人"):
        alias = normalized.replace("/绑定负责人", "", 1).strip()
        target_open_id = _target_mention_open_id(mentions, sender_open_id)
        if target_open_id:
            for mention in mentions or []:
                for key in ("name", "key", "id"):
                    value = str(mention.get(key) or "").strip()
                    if value:
                        alias = alias.replace(f"@{value}", "").replace(value, "").strip()
        if not alias:
            return CommandResult("请发送：/绑定负责人 名字或岗位 @员工，例如 /绑定负责人 Shawn @张三")
        try:
            bound_alias = bind_assignee(alias, target_open_id or sender_open_id or "")
        except ValueError as exc:
            return CommandResult(f"绑定失败：{exc}")
        if target_open_id:
            return CommandResult(f"已绑定负责人：{bound_alias}\n之后 AI 拆出的负责人匹配到这个名称时，会自动分配给被 @ 的同事。")
        return CommandResult(f"已绑定负责人：{bound_alias}\n之后 AI 拆出的负责人匹配到这个名称时，会自动分配给你。")

    if normalized.startswith("/解绑负责人"):
        alias = normalized.replace("/解绑负责人", "", 1).strip()
        if not alias:
            return CommandResult("请发送：/解绑负责人 名字或岗位")
        if unbind_assignee(alias):
            return CommandResult(f"已解绑负责人：{alias}")
        return CommandResult(f"没有找到这个负责人映射：{alias}")

    if normalized in {"/负责人列表", "负责人列表"}:
        aliases = list_assignees()
        if not aliases:
            return CommandResult("还没有负责人映射。请让同事发送：/绑定负责人 名字或岗位")
        return CommandResult("已绑定负责人：\n" + "\n".join(f"- {alias}" for alias in aliases))

    if normalized.startswith("/问") or normalized.startswith("/ai"):
        if normalized.startswith("/问"):
            prompt = normalized.replace("/问", "", 1).strip()
        else:
            prompt = normalized.replace("/ai", "", 1).strip()
        return await _ask(prompt)

    if normalized.startswith("/读文档"):
        document_id = _extract_docx_id(normalized)
        if not document_id:
            return CommandResult(_doc_link_help("/读文档"))

        try:
            content = await feishu_client.get_docx_raw_content(document_id)
        except Exception as exc:
            return CommandResult(
                "读取文档失败。\n"
                "请确认：\n"
                "1. 链接是飞书新版文档 docx，不是示例链接；\n"
                "2. 机器人有读取该文档的权限；\n"
                "3. 应用已开通云文档读取权限。\n\n"
                f"错误信息：{exc}"
            )

        preview = content[:1200] if content else "文档为空或机器人没有读取权限。"
        return CommandResult(f"已读取文档，前 1200 字如下：\n\n{preview}")

    if normalized.startswith("/创建飞书任务"):
        title = normalized.replace("/创建飞书任务", "", 1).strip()
        if not title:
            return CommandResult("请发送：/创建飞书任务 任务标题")

        try:
            result = await feishu_client.create_task(title, assignee_open_id=sender_open_id)
        except Exception as exc:
            return CommandResult(
                "创建飞书任务失败。\n"
                "请确认应用已开通任务相关权限，并已重新发布版本。\n\n"
                f"错误信息：{exc}"
            )

        task = result.get("data", {}).get("task") or result.get("data", {})
        guid = task.get("guid") or task.get("task_guid") or "未知"
        return CommandResult(f"已创建飞书任务：{title}\n任务 ID：{guid}")

    if normalized.startswith("/生成飞书任务"):
        document_id = _extract_docx_id(normalized)
        if not document_id:
            return CommandResult(_doc_link_help("/生成飞书任务"))

        try:
            content = await feishu_client.get_docx_raw_content(document_id)
        except Exception as exc:
            return CommandResult(
                "读取文档失败，无法生成飞书任务。\n"
                "请确认机器人能访问该文档，并且应用已开通云文档读取权限。\n\n"
                f"错误信息：{exc}"
            )

        if not content.strip():
            return CommandResult("文档内容为空，无法生成飞书任务。")

        try:
            result = await create_feishu_tasks_from_doc(content)
        except Exception as exc:
            return CommandResult(
                "生成飞书任务失败。\n"
                "请确认：\n"
                "1. AI API 当前可用；\n"
                "2. 应用已开通飞书任务创建权限；\n"
                "3. 文档内容包含可拆解的项目需求或任务。\n\n"
                f"错误信息：{exc}"
            )

        failed_text = ""
        if result["failed_count"]:
            failed_text = "\n部分任务失败：\n" + "\n".join(result["failed"])

        return CommandResult(
            "已根据文档创建飞书任务。\n"
            f"成功：{result['created_count']} 个\n"
            f"失败：{result['failed_count']} 个"
            f"{failed_text}"
        )

    if _is_task_table_intent(normalized):
        document_id = _extract_docx_id(normalized)
        if document_id:
            try:
                content = await feishu_client.get_docx_raw_content(document_id)
            except Exception as exc:
                return CommandResult(
                "读取文档失败，无法生成任务表。\n"
                "请确认机器人能访问该文档，并且应用已开通云文档读取权限。\n\n"
                f"错误信息：{exc}"
                )
        else:
            try:
                document_file_info = file_info
                document_message_id = message_id
                if document_file_info is None and FEISHU_FILE_RE.search(normalized):
                    document_file_info = {"url": normalized, "file_name": "attachment.pdf"}
                if document_file_info is None:
                    document_message_id, document_file_info = get_recent_file(chat_id, sender_open_id)
                content = await _read_attached_document(document_message_id, document_file_info)
            except Exception as exc:
                return CommandResult(f"读取文件失败，无法生成任务表。\n请确认应用已开通读取消息资源权限。\n\n错误信息：{exc}")
            if content is None:
                return CommandResult(
                    "没有找到可读取的文件。\n"
                    "请先用飞书输入框的“+ / 本地文件”上传 PDF 或 docx，"
                    "然后在 10 分钟内发送 /生成任务表。"
                )

        if not content.strip():
            return CommandResult("文档内容为空，无法生成任务表。")

        try:
            result = await create_task_table_from_doc(
                "项目",
                content,
            )
        except Exception as exc:
            return CommandResult(
                "生成任务表失败。\n"
                "请确认：\n"
                "1. AI API 当前可用；\n"
                "2. 应用已开通 bitable:app 和 base:app:create；\n"
                "3. 文档内容包含可拆解的项目需求或任务。\n\n"
                f"错误信息：{exc}"
            )

        link = _base_link(
            _extract_feishu_origin(normalized),
            result["app_token"],
            result["table_id"],
        )
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
            task_sync_text += "\n未绑定负责人，飞书任务已创建为未分配：\n" + "、".join(result["unmapped_owners"])


        return CommandResult(
            "已根据文档生成任务表。\n"
            f"任务数量：{result['task_count']}\n"
            f"app_token: {result['app_token']}\n"
            f"table_id: {result['table_id']}"
            f"{link_text}"
            f"{task_sync_text}"
        )

    if normalized.startswith("/生成项目表"):
        project_name = normalized.replace("/生成项目表", "", 1).strip() or "新项目"
        try:
            app = await feishu_client.create_bitable_app(f"{project_name} 项目管理")
        except Exception as exc:
            return CommandResult(
                "生成项目表失败：应用还没有开通多维表格权限。\n\n"
                "请到飞书开放平台为当前应用开通这些权限后重新发布：\n"
                "1. bitable:app\n"
                "2. base:app:create\n\n"
                "中文权限一般是：多维表格应用相关权限、创建多维表格应用。\n\n"
                f"错误信息：{exc}"
            )

        app_token = app.get("data", {}).get("app", {}).get("app_token")
        if not app_token:
            return CommandResult(f"多维表格已创建，但未拿到 app_token：{app}")

        await feishu_client.create_project_table(app_token)
        return CommandResult(
            f"已创建「{project_name} 项目管理」多维表格。\n"
            f"app_token: {app_token}\n"
            "如果需要直接打开链接，请使用 /生成任务表 文档链接，或告诉我你的飞书企业域名。"
        )

    if not normalized.startswith("/"):
        return await _ask(normalized)

    return CommandResult("我还不认识这个指令。发送 /帮助 查看可用指令。")
