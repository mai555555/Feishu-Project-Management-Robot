import os
import re
from dataclasses import dataclass

from app.feishu_client import feishu_client
from app.services.ai_client import ask_ai
from app.services.audit_log_service import log_audit, recent_audit_logs
from app.services.assignee_mapping import bind_assignee, get_assignee_mapping, known_assignee_aliases, list_assignees, role_scope_text, unbind_assignee
from app.services.contact_sync_service import sync_feishu_contacts
from app.services.intent_router import classify_intent, is_assignee_binding_intent
from app.services.pending_action_store import pop_pending_action, remember_pending_action
from app.services.project_service import create_feishu_tasks_from_doc, create_task_table_from_doc
from app.services.project_context_service import describe_project_context, describe_project_daily_summary, remember_project_context
from app.services.recent_file_store import remember_recent_file
from app.services.tavily_service import TavilyNotConfiguredError, search_web
from app.services.memory_service import build_memory_prompt, remember_turn
from app.services.organization_service import (
    can_bind_assignee,
    can_manage_org,
    can_set_project_owner,
    describe_member,
    list_members as list_org_members,
    normalize_role,
    permission_denied_text,
    role_label,
    upsert_member,
)
from app.services.task_memory_service import recent_task_scope_for_alias
from app.services.task_table_sync import sync_recent_task_table_assignee
from app.services.self_task_update_service import is_self_task_update_intent, update_my_task_from_text
from app.services.toolkits.document_tools import (
    FEISHU_FILE_RE,
    doc_link_help,
    extract_base_link,
    extract_docx_id,
    extract_feishu_origin,
    file_info_from_payload,
    is_remember_task_table_intent,
    read_attached_document,
    resolve_document_content,
)
from app.services.toolkits.intent_tools import (
    TASK_TABLE_INTENT_KEYWORDS,
    is_cancel_text,
    is_confirm_text,
    is_task_table_intent,
    is_work_log_table_intent,
)
from app.services.toolkits.task_table_tools import (
    build_base_link,
    extract_project_name,
    format_task_query_result,
    format_task_table_result,
    normalize_project_id,
    query_tasks_by_alias,
    query_tasks_by_assignee,
    query_unassigned_tasks,
    remember_existing_task_table,
    remember_generated_task_table,
)


BOT_MENTION_NAMES = tuple(
    name.strip()
    for name in os.getenv("FEISHU_BOT_NAMES", "麦草莓").split(",")
    if name.strip()
)

# Common document, intent and task-table helpers live in app.services.toolkits.


@dataclass(frozen=True)
class CommandResult:
    text: str



def _extract_docx_id(text: str) -> str | None:
    return extract_docx_id(text)


def _extract_feishu_origin(text: str) -> str | None:
    return extract_feishu_origin(text)


def _extract_base_link(text: str):
    info = extract_base_link(text)
    if not info:
        return None
    return info.app_token, info.table_id, info.link


def _is_remember_task_table_intent(text: str) -> bool:
    return is_remember_task_table_intent(text)


def _base_link(origin: str | None, app_token: str, table_id: str | None = None) -> str:
    return build_base_link(origin, app_token, table_id)


def _is_task_table_intent(text: str) -> bool:
    return is_task_table_intent(text)


def _is_work_log_table_intent(text: str) -> bool:
    return is_work_log_table_intent(text)


def _is_confirm_text(text: str) -> bool:
    return is_confirm_text(text)


def _is_cancel_text(text: str) -> bool:
    return is_cancel_text(text)


def _file_info_from_payload(payload: object) -> dict[str, str] | None:
    return file_info_from_payload(payload)

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


def _is_bot_mention(mention: dict[str, object]) -> bool:
    if not BOT_MENTION_NAMES:
        return False
    names = [str(mention.get(key) or "").strip().lstrip("@") for key in ("name", "key")]
    return any(name in BOT_MENTION_NAMES for name in names)


def _target_mention_open_id(
    mentions: list[dict[str, object]] | None,
    sender_open_id: str | None,
) -> str | None:
    candidates = [
        open_id
        for mention in mentions or []
        if (open_id := _mention_open_id(mention))
        and open_id != sender_open_id
        and not _is_bot_mention(mention)
    ]
    if candidates:
        return candidates[-1]
    return None


def _mention_display_name(mention: dict[str, object]) -> str:
    for key in ("name", "key"):
        value = mention.get(key)
        if value:
            return str(value).strip()
    mention_id = mention.get("id")
    if isinstance(mention_id, dict):
        value = mention_id.get("name") or mention_id.get("union_id") or mention_id.get("open_id")
        if value:
            return str(value).strip()
    return ""



BIND_ASSIGNEE_PREFIXES = ("/绑定负责人", "绑定负责人", "绑定")
BIND_ASSIGNEE_KEYWORDS = ("负责人", "分配给", "交给", "绑定给", "负责", "设为", "设置")
SHORT_BINDING_BLOCK_WORDS = (
    "?",
    "？",
    "吗",
    "呢",
    "吧",
    "怎么",
    "为什么",
    "如何",
    "什么",
    "谁",
    "你",
    "我",
    "他",
    "她",
    "它",
    "机器人",
    "介绍",
    "认识",
    "请问",
    "帮我",
    "看看",
    "看下",
    "处理",
    "确认",
    "收到",
    "好的",
)
SHORT_BINDING_ALLOWED_KEYWORDS = (
    "前端",
    "后端",
    "测试",
    "产品",
    "设计",
    "运营",
    "UI",
    "ui",
    "UX",
    "ux",
    "接口",
    "API",
    "api",
    "数据库",
    "服务端",
    "客户端",
    "小程序",
    "管理后台",
    "后台",
    "移动端",
    "安卓",
    "Android",
    "android",
    "iOS",
    "ios",
    "运维",
    "DevOps",
    "devops",
    "架构",
    "算法",
    "数据",
    "需求",
    "文档",
    "项目",
    "客服",
    "财务",
    "行政",
    "法务",
)


def _remove_mention_text(text: str, mentions: list[dict[str, object]] | None) -> str:
    cleaned = text
    for mention in mentions or []:
        for key in ("name", "key", "id"):
            value = str(mention.get(key) or "").strip()
            if value:
                cleaned = cleaned.replace(f"@{value}", " ").replace(value, " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _command_text(text: str, mentions: list[dict[str, object]] | None) -> str:
    return _remove_mention_text(text, mentions).strip()


def _is_assignee_list_command(text: str) -> bool:
    normalized = text.strip()
    return normalized in {"/负责人列表", "负责人列表"}


def _clean_assignee_alias(text: str, mentions: list[dict[str, object]] | None) -> str:
    alias = _remove_mention_text(text, mentions)
    replacements = (
        "/绑定负责人",
        "绑定负责人",
        "绑定一下",
        "绑定",
        "把",
        "请",
        "帮我",
        "负责人是",
        "负责人为",
        "负责人",
        "分配给",
        "绑定给",
        "交给",
        "负责",
        "设为",
        "设置为",
        "改为",
        "改成",
        "换成",
        "设置",
        "给",
        "是",
        "为",
    )
    for item in replacements:
        alias = alias.replace(item, " ")
    alias = re.sub(r"[：:，,。.!！?？]", " ", alias)
    return re.sub(r"\s+", "", alias).strip()


def _looks_like_allowed_assignee_alias(alias: str) -> bool:
    normalized_alias = alias.strip()
    if not normalized_alias:
        return False
    return any(keyword in normalized_alias for keyword in SHORT_BINDING_ALLOWED_KEYWORDS)


def _looks_like_short_assignee_binding(text: str, alias: str) -> bool:
    if not alias:
        return False
    if len(alias) > 20:
        return False
    if text.lstrip().startswith("/"):
        return False
    if _is_task_table_intent(text) or _is_assignee_list_command(text):
        return False
    if any(word in text for word in SHORT_BINDING_BLOCK_WORDS):
        return False
    return _looks_like_allowed_assignee_alias(alias)





def _extract_task_query_person(text: str, target_display_name: str = "") -> str:
    if target_display_name:
        return target_display_name.strip().lstrip("@")
    cleaned = re.sub(r"@", " ", text).strip()
    patterns = (
        r"([^\s，,。？?；;]+)\s*(?:负责什么|负责的任务|有什么任务|有哪些任务)",
        r"(?:查一下|查询|列出)?\s*([^\s，,。？?；;]+)\s*的任务",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate not in {"我", "他", "她", "谁", "哪些"}:
                return candidate
    return ""


def _extract_task_query_alias(text: str, chat_id: str | None = None) -> str:
    compact = re.sub(r"\s+", "", text).lower()
    for alias in sorted(known_assignee_aliases(chat_id=chat_id), key=len, reverse=True):
        if alias and alias.lower() in compact:
            return alias
    for keyword in SHORT_BINDING_ALLOWED_KEYWORDS:
        normalized_keyword = re.sub(r"\s+", "", keyword).lower()
        if normalized_keyword and normalized_keyword in compact:
            return keyword
    return ""


def _answer_task_query(text: str, chat_id: str | None, target_display_name: str = "") -> str:
    compact = re.sub(r"\s+", "", text)
    project_name = extract_project_name(text)
    if any(word in compact for word in ("未分配", "没分配", "待分配", "待定")):
        return format_task_query_result(
            title="未分配任务",
            query_result=query_unassigned_tasks(chat_id, project_name=project_name),
            project_name=project_name,
        )

    person = _extract_task_query_person(text, target_display_name)
    if person:
        return format_task_query_result(
            title=f"{person} 负责的任务",
            query_result=query_tasks_by_assignee(chat_id, person, project_name=project_name),
            project_name=project_name,
        )

    alias = _extract_task_query_alias(text, chat_id)
    if alias:
        return format_task_query_result(
            title=f"{alias}相关任务",
            query_result=query_tasks_by_alias(chat_id, alias, project_name=project_name),
            project_name=project_name,
        )

    return "你想查哪类任务？可以这样问：官网项目哪些任务还没分配、张子扬负责什么、官网项目后端有哪些任务。"

def _extract_alias_from_assignee_binding_text(text: str, chat_id: str | None = None) -> str:
    compact = re.sub(r"\s+", "", text).lower()
    aliases = sorted(known_assignee_aliases(chat_id=chat_id), key=len, reverse=True)
    for alias in aliases:
        if alias and alias.lower() in compact:
            return alias
    for keyword in SHORT_BINDING_ALLOWED_KEYWORDS:
        normalized_keyword = re.sub(r"\s+", "", keyword).lower()
        if normalized_keyword and normalized_keyword in compact:
            return keyword
    return ""


def _looks_like_assignee_update_without_mention(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if any(word in compact for word in ("?", "？", "吗", "呢", "怎么", "为什么", "如何", "什么", "问题")):
        return False
    if any(word in compact for word in ("谁负责", "负责吗", "是不是负责", "谁来负责")):
        return False
    return "负责人" in compact and any(word in compact for word in ("是", "为", "设为", "设置", "改为", "改成", "换成", "交给"))

def _extract_assignee_question_alias(text: str, chat_id: str | None = None) -> str:
    compact = re.sub(r"\s+", "", text).lower()
    for alias in known_assignee_aliases(chat_id=chat_id):
        if alias and alias.lower() in compact:
            return alias
    return ""


def _extract_question_person(text: str, target_display_name: str = "") -> str:
    if target_display_name:
        return target_display_name.strip().lstrip("@")
    cleaned = re.sub(r"@", " ", text).strip()
    patterns = (
        r"负责人(?:是|是不是|是否是|为)?\s*([^吗？?，,。；;\s]+)",
        r"([^吗？?，,。；;\s]+)\s*(?:负责吗|是不是负责|是否负责)",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            candidate = match.group(1).strip().lstrip("@")
            if candidate and candidate not in {"谁", "哪个", "哪位"}:
                return candidate
    return ""


def _answer_assignee_question(text: str, chat_id: str | None, target_display_name: str = "") -> str:
    alias = _extract_assignee_question_alias(text, chat_id)
    person = _extract_question_person(text, target_display_name)

    if not alias:
        aliases = list_assignees(chat_id=chat_id)
        if aliases:
            return "我查到目前的负责人规则是：\n" + "\n".join(f"- {item}" for item in aliases)
        return "我这里还没有负责人规则。你可以直接告诉我，比如：前端负责人是 @张三。"

    mapping_item = get_assignee_mapping(alias, chat_id=chat_id)
    scope = recent_task_scope_for_alias(alias, chat_id)
    modules = scope.get("modules") or []
    titles = scope.get("titles") or []
    scope_hint = role_scope_text(alias)

    if mapping_item:
        matched_alias, info = mapping_item
        current_name = (info.get("display_name") or "已绑定的同事").strip()
        if person:
            same_person = person in current_name or current_name in person
            first_line = (
                f"是的，当前“{matched_alias}”负责人是 {current_name}。"
                if same_person
                else f"不是。当前“{matched_alias}”负责人是 {current_name}，不是 {person}。"
            )
        else:
            first_line = f"当前“{matched_alias}”负责人是 {current_name}。"
    else:
        first_line = f"我在负责人规则里还没有找到“{alias}”的绑定。"

    details = []
    if modules:
        details.append("从最近生成的任务表看，主要涉及模块：" + "、".join(str(item) for item in modules[:5]) + "。")
    elif scope_hint:
        details.append(f"按当前模块规则，“{alias}”通常覆盖：{scope_hint}。")

    if titles:
        details.append("相关任务包括：" + "、".join(str(item) for item in titles[:5]) + "。")

    return "\n".join([first_line, *details])


def _extract_org_role_from_text(text: str) -> str | None:
    for role_word in ("管理员", "管理人员", "公司管理者", "项目经理", "项目负责人", "主管", "普通员工", "员工", "成员"):
        if role_word in text:
            return normalize_role(role_word)
    return None


def _is_contact_sync_intent(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(word in compact for word in ("同步公司通讯录", "同步飞书通讯录", "同步组织架构", "更新公司通讯录", "更新组织架构"))


def _is_org_role_set_intent(text: str) -> bool:
    if not any(word in text for word in ("设为", "设置为", "改为", "改成", "设成", "设置成")):
        return False
    return _extract_org_role_from_text(text) is not None


def _is_org_list_intent(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in {
        "管理员列表",
        "管理人员列表",
        "成员列表",
        "员工列表",
        "组织成员",
        "组织结构",
        "谁是管理员",
        "有哪些管理员",
        "谁是项目经理",
        "项目经理列表",
    }


def _is_org_role_question(text: str, target_open_id: str | None) -> bool:
    if not target_open_id:
        return False
    return any(word in text for word in ("什么角色", "是什么角色", "权限", "是不是管理员", "是不是项目经理"))


def _is_audit_log_intent(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in {"审计日志", "操作日志", "最近操作", "最近操作记录", "谁改了什么"}


def _format_audit_logs() -> str:
    rows = recent_audit_logs(limit=10)
    if not rows:
        return "暂时还没有操作记录。"
    action_names = {
        "sync_contacts": "同步通讯录",
        "set_org_role": "设置组织角色",
        "bind_assignee": "设置负责人",
        "unbind_assignee": "解绑负责人",
        "create_task_table": "生成任务表",
        "create_single_feishu_task": "创建飞书任务",
        "create_feishu_tasks": "生成飞书任务",
    }
    lines = []
    for item in rows:
        action = action_names.get(item.get("action", ""), item.get("action", "操作"))
        actor = item.get("actor_open_id") or "未知用户"
        target = item.get("target_name") or item.get("target_open_id") or ""
        target_text = f"，对象：{target}" if target else ""
        lines.append(f"- {action}，操作人：{actor[-8:]}{target_text}")
    return "最近操作记录：\n" + "\n".join(lines)


def _extract_search_query(text: str) -> str:
    query = text.strip()
    prefixes = (
        "/搜索",
        "搜索一下",
        "搜索",
        "联网搜索一下",
        "联网搜索",
        "联网查一下",
        "联网查",
        "查一下",
        "帮我搜索一下",
        "帮我搜索",
        "帮我联网查一下",
        "帮我联网查",
    )
    for prefix in sorted(prefixes, key=len, reverse=True):
        if query.startswith(prefix):
            query = query.replace(prefix, "", 1).strip()
            break
    return query.strip(" ：:，,。")


async def _handle_org_command(
    command_text: str,
    *,
    sender_open_id: str | None,
    target_open_id: str | None,
    target_display_name: str,
) -> CommandResult | None:
    if _is_contact_sync_intent(command_text):
        if not can_manage_org(sender_open_id):
            return CommandResult(permission_denied_text("同步公司通讯录"))
        try:
            result = await sync_feishu_contacts(updated_by_open_id=sender_open_id)
        except Exception as exc:
            return CommandResult(
                "同步飞书通讯录失败。\n"
                "请确认应用已经开通通讯录读取权限，并重新发布版本。\n\n"
                f"错误信息：{exc}"
            )
        log_audit(
            "sync_contacts",
            actor_open_id=sender_open_id,
            details={
                "departments": result.get("departments", 0),
                "users": result.get("users", 0),
                "department_leaders": result.get("department_leaders", 0),
                "total_members": result.get("total_members", 0),
            },
        )
        failed = result.get("failed_departments") or []
        failed_text = ""
        if failed:
            failed_text = "\n部分部门同步失败：\n" + "\n".join(str(item) for item in failed)
        missing_name_count = int(result.get("missing_name_count") or 0)
        permission_hint = ""
        if missing_name_count:
            permission_hint = (
                f"\n提示：有 {missing_name_count} 个成员没有返回姓名，当前通讯录权限可能只开放了 ID。"
                "请在飞书开放平台补充用户姓名/基础信息、部门信息权限并重新发布应用。"
            )
        return CommandResult(
            "飞书通讯录同步完成。\n"
            f"同步部门：{result.get('departments', 0)} 个\n"
            f"同步员工：{result.get('users', 0)} 人\n"
            f"识别部门负责人：{result.get('department_leaders', 0)} 人\n"
            f"当前组织成员总数：{result.get('total_members', 0)} 人"
            f"{permission_hint}"
            f"{failed_text}"
        )

    if _is_org_list_intent(command_text):
        role = None
        if "管理员" in command_text or "管理人员" in command_text:
            role = "admin"
        elif "项目经理" in command_text:
            role = "manager"
        members = list_org_members(role=role)
        if not members:
            return CommandResult("组织成员表里还没有记录。可以先说：把 @张三 设为管理员。")
        title = f"当前{role_label(role)}列表" if role else "当前组织成员"
        return CommandResult(title + "：\n" + "\n".join(f"- {item}" for item in members))

    if _is_org_role_question(command_text, target_open_id):
        return CommandResult(describe_member(target_open_id, target_display_name))

    if _is_org_role_set_intent(command_text):
        role = _extract_org_role_from_text(command_text)
        if not target_open_id:
            return CommandResult("要设置组织角色，需要 @ 具体同事。比如：把 @张三 设为项目经理。")
        if not can_manage_org(sender_open_id):
            return CommandResult(permission_denied_text("维护组织成员角色"))
        item = upsert_member(
            target_open_id,
            target_display_name,
            role=role or "employee",
            updated_by_open_id=sender_open_id,
        )
        log_audit(
            "set_org_role",
            actor_open_id=sender_open_id,
            target_open_id=target_open_id,
            target_name=target_display_name,
            details={"role": item["role"], "role_label": item["role_label"]},
        )
        return CommandResult(f"已记录组织角色：{item['display_name'] or target_display_name} -> {item['role_label']}。")

    return None

def _extract_bind_assignee_alias(
    text: str,
    mentions: list[dict[str, object]] | None,
    target_open_id: str | None,
) -> str | None:
    normalized = text.strip()
    command_text = _command_text(normalized, mentions)
    if _is_task_table_intent(command_text) or _is_assignee_list_command(command_text):
        return None
    if normalized.startswith(BIND_ASSIGNEE_PREFIXES):
        return _clean_assignee_alias(normalized, mentions)
    if is_assignee_binding_intent(normalized, has_target_mention=bool(target_open_id)):
        return _clean_assignee_alias(normalized, mentions)
    return None

def _help_text() -> str:
    return (
        "你好，我是麦草莓，可以帮你把项目资料变成可跟进的任务。\n\n"
        "你可以直接这样说：\n\n"
        "“帮我把这份文档拆成任务”\n"
        "“根据这个 PDF 做项目计划”\n"
        "“前端负责人是 @张三”\n"
        "“查一下飞书任务管理 API”\n"
        "“总结一下这份资料”\n\n"
        "把文档、Word 或 PDF 发给我，再告诉我你想做什么就行。"
    )


async def _bind_assignee_rule(
    *,
    bind_alias: str,
    target_open_id: str,
    target_display_name: str,
    sender_open_id: str | None,
    chat_id: str | None,
    project_name: str | None = None,
    project_id: str | None = None,
    is_project_owner_binding: bool = False,
) -> CommandResult:
    try:
        bound_alias = bind_assignee(
            bind_alias,
            target_open_id,
            target_display_name,
            chat_id=chat_id,
            project_id=project_id,
            updated_by_open_id=sender_open_id,
        )
    except ValueError as exc:
        return CommandResult(f"绑定失败：{exc}")

    name = target_display_name or "被 @ 的同事"
    log_audit(
        "bind_assignee",
        actor_open_id=sender_open_id,
        chat_id=chat_id,
        target_open_id=target_open_id,
        target_name=name,
        details={
            "alias": bound_alias,
            "project_name": project_name or "",
            "project_id": project_id or "",
            "is_project_owner_binding": is_project_owner_binding,
        },
    )
    if is_project_owner_binding:
        return CommandResult(
            f"已记录“{project_name}”项目级负责人：{name}。\n"
            "项目级负责人只代表管理归属，不会把所有任务都分配给 TA；具体执行还是按职责标签分配。"
        )

    sync_text = ""
    try:
        sync_result = await sync_recent_task_table_assignee(
            chat_id,
            bound_alias,
            name,
            project_name=project_name,
            project_id=project_id,
        )
        updated_count = int(sync_result.get("updated_count") or 0)
        link = str(sync_result.get("link") or "")
        if updated_count > 0:
            scope_note = ""
            if sync_result.get("table_scope") == "global":
                scope_note = "（当前群没有任务表记录，已使用全局最近任务表）"
            sync_text = f"\n已同步更新最近任务表{scope_note}：{updated_count} 条。"
            if link:
                sync_text += f"\n打开链接：{link}"
        elif sync_result.get("reason") == "no_recent_table":
            sync_text = "\n当前还没有最近任务表；之后新生成的任务表会按这个负责人规则分配。"
        else:
            scope_note = ""
            if sync_result.get("table_scope") == "global":
                scope_note = "（已检查全局最近任务表）"
            sync_text = f"\n最近任务表里暂时没有匹配到这个职责标签{scope_note}；之后新生成的任务表会按这个规则分配。"
    except Exception as exc:
        sync_text = f"\n负责人规则已保存，但同步最近任务表失败：{exc}"

    project_prefix = f"“{project_name}”项目里" if project_name else ""
    return CommandResult(
        f"好的，之后{project_prefix}“{bound_alias}”相关的任务会优先分配给 {name}。"
        f"{sync_text}"
    )


async def _ask(
    prompt: str,
    *,
    chat_id: str | None = None,
    sender_open_id: str | None = None,
) -> CommandResult:
    if not prompt:
        return CommandResult("你可以直接把问题发给我，比如：帮我总结一下这份资料。")

    memory_prompt = build_memory_prompt(
        prompt,
        chat_id=chat_id,
        sender_open_id=sender_open_id,
    )
    try:
        answer = await ask_ai(memory_prompt)
    except Exception as exc:
        return CommandResult(f"AI 请求失败：{exc}")

    answer = _guard_plain_chat_answer(answer)[:3800]
    remember_turn(chat_id, sender_open_id, prompt, answer)
    return CommandResult(answer)



def _is_project_context_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    exact = {
        "\u6700\u8fd1\u4efb\u52a1\u8868",
        "\u6700\u8fd1\u7684\u4efb\u52a1\u8868",
        "\u4efb\u52a1\u8868\u5728\u54ea",
        "\u4efb\u52a1\u8868\u94fe\u63a5",
        "\u5f53\u524d\u9879\u76ee",
        "\u73b0\u5728\u9879\u76ee",
        "\u9879\u76ee\u60c5\u51b5",
        "\u9879\u76ee\u8fdb\u5ea6",
        "\u8fd9\u4e2a\u9879\u76ee\u73b0\u5728\u600e\u4e48\u6837",
        "\u8fd9\u4e2a\u9879\u76ee\u4ec0\u4e48\u60c5\u51b5",
        "\u6700\u8fd1\u751f\u6210\u4e86\u4ec0\u4e48",
        "\u521a\u624d\u751f\u6210\u7684\u8868",
    }
    if compact in exact:
        return True
    phrases = (
        "\u6700\u8fd1\u4efb\u52a1\u8868",
        "\u6700\u8fd1\u7684\u4efb\u52a1\u8868",
        "\u4efb\u52a1\u8868\u94fe\u63a5",
        "\u6253\u5f00\u4efb\u52a1\u8868",
        "\u4efb\u52a1\u8868\u5728\u54ea",
        "\u4efb\u52a1\u8868\u7ed9\u6211",
        "\u53d1\u4e00\u4e0b\u4efb\u52a1\u8868",
        "\u5f53\u524d\u9879\u76ee\u662f\u4ec0\u4e48",
        "\u73b0\u5728\u5728\u505a\u54ea\u4e2a\u9879\u76ee",
        "\u8fd9\u4e2a\u7fa4\u5728\u505a\u54ea\u4e2a\u9879\u76ee",
        "\u8fd9\u4e2a\u9879\u76ee\u73b0\u5728\u600e\u4e48\u6837",
        "\u8fd9\u4e2a\u9879\u76ee\u4ec0\u4e48\u60c5\u51b5",
        "\u9879\u76ee\u73b0\u5728\u600e\u4e48\u6837",
        "\u9879\u76ee\u8fdb\u5ea6\u600e\u4e48\u6837",
    )
    return any(phrase in compact for phrase in phrases)


def _is_project_summary_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    exact = {
        "\u4eca\u65e5\u9879\u76ee\u603b\u7ed3",
        "\u4eca\u5929\u9879\u76ee\u603b\u7ed3",
        "\u9879\u76ee\u65e5\u62a5",
        "\u4eca\u65e5\u65e5\u62a5",
        "\u4eca\u5929\u65e5\u62a5",
        "\u9879\u76ee\u603b\u7ed3",
        "\u603b\u7ed3\u9879\u76ee",
        "\u8fd9\u4e2a\u9879\u76ee\u603b\u7ed3",
    }
    if compact in exact:
        return True
    phrases = (
        "\u4eca\u65e5\u9879\u76ee\u603b\u7ed3",
        "\u4eca\u5929\u9879\u76ee\u603b\u7ed3",
        "\u9879\u76ee\u65e5\u62a5",
        "\u9879\u76ee\u603b\u7ed3",
        "\u603b\u7ed3\u4e00\u4e0b\u9879\u76ee",
        "\u603b\u7ed3\u4e00\u4e0b\u8fd9\u4e2a\u9879\u76ee",
        "\u8fd9\u4e2a\u9879\u76ee\u4eca\u5929\u600e\u4e48\u6837",
        "\u4eca\u5929\u9879\u76ee\u600e\u4e48\u6837",
        "\u6c47\u603b\u4e00\u4e0b\u9879\u76ee",
    )
    return any(phrase in compact for phrase in phrases)


def _guard_plain_chat_answer(answer: str) -> str:
    risky_action_words = (
        "已创建",
        "已经创建",
        "创建好了",
        "已生成",
        "已经生成",
        "已同步",
        "已经同步",
        "已绑定",
        "已经绑定",
        "点击此处访问",
    )
    link_words = ("app_token", "table_id", "/base/", "多维表格", "飞书任务")
    if any(word in answer for word in risky_action_words) and any(word in answer for word in link_words):
        return (
            "我可以帮你处理这件事，但需要先确认一下具体操作。"
            "请直接告诉我想创建什么表、生成什么任务，或回复更明确的操作目标。"
        )
    return answer

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
    command_text = _command_text(normalized, mentions)
    target_open_id_for_intent = _target_mention_open_id(mentions, sender_open_id)
    intent = classify_intent(command_text, has_target_mention=bool(target_open_id_for_intent))

    if file_info:
        remember_recent_file(chat_id, sender_open_id, message_id, file_info)
        remember_project_context(
            chat_id=chat_id,
            sender_open_id=sender_open_id,
            latest_file_name=file_info.get("file_name") or "\u6587\u4ef6",
        )
        if not _is_task_table_intent(command_text) and intent.name != "create_feishu_tasks":
            file_name = file_info.get("file_name") or "文件"
            return CommandResult(
                f"我收到文件了：{file_name}\n"
                "接下来你可以直接说“帮我拆成任务”或“根据这份资料做项目计划”。"
            )

    if _is_remember_task_table_intent(command_text):
        parsed_base = _extract_base_link(command_text)
        if parsed_base:
            app_token, table_id, link = parsed_base
            if not table_id:
                return CommandResult("我看到了多维表格链接，但没有识别到 table 参数。请打开具体表格视图后复制完整链接再发给我。")
            project_name = extract_project_name(command_text)
            remember_existing_task_table(
                chat_id=chat_id,
                sender_open_id=sender_open_id,
                app_token=app_token,
                table_id=table_id,
                link=link,
                project_name=project_name,
            )
            remember_project_context(
                chat_id=chat_id,
                sender_open_id=sender_open_id,
                project_name=project_name,
                project_id=normalize_project_id(project_name),
                app_token=app_token,
                table_id=table_id,
                link=link,
            )
            project_text = f"“{project_name}”项目" if project_name else "当前"
            return CommandResult(f"好的，我已经把这张表记为{project_text}任务表。之后更新负责人时，会优先同步它。\n打开链接：{link}")

    if command_text in {"/帮助", "帮助", "/help"}:
        return CommandResult(_help_text())

    if _is_project_summary_query(command_text):
        return CommandResult(await describe_project_daily_summary(chat_id, sender_open_id))

    if _is_project_context_query(command_text):
        return CommandResult(describe_project_context(chat_id, sender_open_id))

    if is_self_task_update_intent(command_text):
        return CommandResult(await update_my_task_from_text(command_text, sender_open_id=sender_open_id, chat_id=chat_id))

    pending_action, pending_payload = pop_pending_action(chat_id, sender_open_id)
    if pending_action:
        if _is_cancel_text(command_text):
            if pending_action == "confirm_rebind_assignee":
                return CommandResult("好，那这个负责人先不改。")
            return CommandResult("好的，先不创建。你后面需要时再告诉我。")
        if _is_confirm_text(command_text):
            if pending_action == "create_work_log_table":
                try:
                    app = await feishu_client.create_bitable_app("工作日志多维表格")
                    app_token = app.get("data", {}).get("app", {}).get("app_token")
                    if not app_token:
                        return CommandResult(f"多维表格已创建，但没有拿到 app_token：{app}")
                    table = await feishu_client.create_work_log_table(app_token)
                    table_id = (
                        table.get("data", {}).get("table_id")
                        or table.get("data", {}).get("table", {}).get("table_id")
                    )
                except Exception as exc:
                    return CommandResult(
                        "创建工作日志表失败。\n"
                        "请确认应用已经开通多维表格创建权限，然后重新发布应用版本。\n\n"
                        f"错误信息：{exc}"
                    )

                link = _base_link(str(pending_payload.get("origin") or ""), app_token, table_id)
                link_text = f"\n打开链接：{link}" if link else "\n已创建成功，但还没有配置企业域名，暂时只能返回 app_token。"
                return CommandResult(
                    "工作日志表已经创建好了。\n"
                    "字段包括：日期、工作事项、完成进度、备注/困难点、负责人。\n"
                    f"app_token: {app_token}"
                    f"{link_text}"
                )
            if pending_action == "confirm_rebind_assignee":
                if not can_bind_assignee(sender_open_id):
                    return CommandResult(permission_denied_text("设置职责负责人"))
                return await _bind_assignee_rule(
                    bind_alias=str(pending_payload.get("bind_alias") or ""),
                    target_open_id=str(pending_payload.get("target_open_id") or ""),
                    target_display_name=str(pending_payload.get("target_display_name") or ""),
                    sender_open_id=sender_open_id,
                    chat_id=chat_id,
                    project_name=str(pending_payload.get("project_name") or "") or None,
                    project_id=str(pending_payload.get("project_id") or "") or None,
                    is_project_owner_binding=bool(pending_payload.get("is_project_owner_binding")),
                )
        remember_pending_action(chat_id, sender_open_id, pending_action, pending_payload)

    target_open_id = target_open_id_for_intent
    target_display_name = ""
    if target_open_id:
        for mention in mentions or []:
            if _mention_open_id(mention) == target_open_id:
                target_display_name = _mention_display_name(mention)
                break

    org_result = await _handle_org_command(
        command_text,
        sender_open_id=sender_open_id,
        target_open_id=target_open_id,
        target_display_name=target_display_name,
    )
    if org_result:
        return org_result

    if _is_audit_log_intent(command_text):
        if not can_manage_org(sender_open_id):
            return CommandResult(permission_denied_text("查看操作日志"))
        return CommandResult(_format_audit_logs())

    if intent.name == "assignee_question":
        return CommandResult(_answer_assignee_question(command_text, chat_id, target_display_name))

    if intent.name == "task_query":
        return CommandResult(_answer_task_query(command_text, chat_id, target_display_name))

    bind_alias = _extract_bind_assignee_alias(normalized, mentions, target_open_id)
    if bind_alias is None and intent.name == "assignee_binding":
        bind_alias = _extract_alias_from_assignee_binding_text(command_text, chat_id)
    if bind_alias is not None:
        if not bind_alias:
            return CommandResult(
                "可以。请告诉我哪个模块由谁负责，比如：\n"
                "“前端负责人是 @张三”\n"
                "“后端交给 @李四”\n"
                "“测试这块由 @王五 负责”"
            )
        if not target_open_id and not normalized.startswith("/绑定负责人"):
            return CommandResult(f"我知道你想更新“{bind_alias}”负责人，但需要 @ 一下具体同事，我才能拿到飞书账号并同步任务。比如：{bind_alias}负责人是 @张三")
        project_name = extract_project_name(command_text)
        project_id = normalize_project_id(project_name) if project_name else None
        is_project_owner_binding = bool(project_name and normalize_project_id(bind_alias) == project_id)
        if is_project_owner_binding:
            if not can_set_project_owner(sender_open_id):
                return CommandResult(
                    "项目级负责人属于公司管理权限，我不能直接替你设置。\n"
                    "你可以让管理员来设置；如果只是任务分配，请改成具体职责负责人，比如：\n"
                    f"“{project_name}后端负责人是 @张三”\n"
                    f"“{project_name}活动执行负责人是 @李四”。"
                )
            bind_alias = "项目负责人"
        elif not can_bind_assignee(sender_open_id):
            return CommandResult(permission_denied_text("设置职责负责人"))

        existing_mapping = get_assignee_mapping(bind_alias, chat_id=chat_id, project_id=project_id)
        if existing_mapping and not is_project_owner_binding:
            existing_alias, existing_info = existing_mapping
            existing_open_id = str(existing_info.get("open_id") or "")
            existing_name = str(existing_info.get("display_name") or "已绑定的同事")
            new_open_id = target_open_id or sender_open_id or ""
            new_name = target_display_name or "被 @ 的同事"
            if existing_open_id and new_open_id and existing_open_id != new_open_id:
                remember_pending_action(
                    chat_id,
                    sender_open_id,
                    "confirm_rebind_assignee",
                    {
                        "bind_alias": bind_alias,
                        "target_open_id": new_open_id,
                        "target_display_name": new_name,
                        "project_name": project_name or "",
                        "project_id": project_id or "",
                        "is_project_owner_binding": False,
                    },
                )
                return CommandResult(
                    f"现在“{existing_alias}”负责人是 {existing_name}，你是想改成 {new_name} 吗？\n"
                    "如果要改，回复“确认”；不改就回复“取消”。"
                )

        return await _bind_assignee_rule(
            bind_alias=bind_alias,
            target_open_id=target_open_id or sender_open_id or "",
            target_display_name=target_display_name or ("被 @ 的同事" if target_open_id else "你"),
            sender_open_id=sender_open_id,
            chat_id=chat_id,
            project_name=project_name,
            project_id=project_id,
            is_project_owner_binding=is_project_owner_binding,
        )

    if normalized.startswith("/解绑负责人"):
        alias = normalized.replace("/解绑负责人", "", 1).strip()
        if not alias:
            return CommandResult("请告诉我要取消哪个负责人规则，比如：解绑负责人 前端")
        if unbind_assignee(alias, chat_id=chat_id):
            log_audit(
                "unbind_assignee",
                actor_open_id=sender_open_id,
                chat_id=chat_id,
                details={"alias": alias},
            )
            return CommandResult(f"已解绑负责人：{alias}")
        return CommandResult(f"没有找到这个负责人映射：{alias}")

    if _is_assignee_list_command(command_text):
        aliases = list_assignees(chat_id=chat_id)
        if not aliases:
            return CommandResult("目前还没有负责人规则。你可以说：前端负责人是 @张三")
        return CommandResult("当前负责人规则：\n" + "\n".join(f"- {alias}" for alias in aliases))

    if _looks_like_assignee_update_without_mention(command_text):
        alias = _extract_alias_from_assignee_binding_text(command_text, chat_id) or "这个模块"
        return CommandResult(f"我知道你想更新“{alias}”负责人，但需要 @ 一下具体同事，我才能拿到飞书账号并同步任务。比如：{alias}负责人是 @张三")

    if intent.name == "search" or command_text.startswith(("/搜索", "搜索", "联网搜索", "联网查", "查一下", "帮我搜索", "帮我联网查")):
        query = _extract_search_query(command_text)
        try:
            return CommandResult(await search_web(query))
        except TavilyNotConfiguredError as exc:
            return CommandResult(str(exc))
        except Exception as exc:
            return CommandResult(f"联网搜索失败：{exc}")

    if command_text.startswith("/问") or command_text.startswith("/ai"):
        if command_text.startswith("/问"):
            prompt = command_text.replace("/问", "", 1).strip()
        else:
            prompt = command_text.replace("/ai", "", 1).strip()
        return await _ask(prompt, chat_id=chat_id, sender_open_id=sender_open_id)

    if command_text.startswith("/读文档"):
        document_id = _extract_docx_id(command_text)
        if not document_id:
            return CommandResult(doc_link_help("/读文档"))

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

    if command_text.startswith("/创建飞书任务"):
        title = command_text.replace("/创建飞书任务", "", 1).strip()
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
        log_audit(
            "create_single_feishu_task",
            actor_open_id=sender_open_id,
            chat_id=chat_id,
            details={"title": title, "guid": guid},
        )
        return CommandResult(f"已创建飞书任务：{title}\n任务 ID：{guid}")

    if command_text.startswith("/生成飞书任务"):
        document_id = _extract_docx_id(command_text)
        if not document_id:
            return CommandResult(doc_link_help("/生成飞书任务"))

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
            result = await create_feishu_tasks_from_doc(content, chat_id=chat_id)
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
        log_audit(
            "create_feishu_tasks",
            actor_open_id=sender_open_id,
            chat_id=chat_id,
            details={
                "created_count": result.get("created_count", 0),
                "failed_count": result.get("failed_count", 0),
            },
        )

        return CommandResult(
            "已根据文档创建飞书任务。\n"
            f"成功：{result['created_count']} 个\n"
            f"失败：{result['failed_count']} 个"
            f"{failed_text}"
        )

    if _is_task_table_intent(command_text):
        try:
            content = await resolve_document_content(
                command_text,
                chat_id=chat_id,
                sender_open_id=sender_open_id,
                message_id=message_id,
                file_info=file_info,
            )
        except Exception as exc:
            return CommandResult(
                "读取资料失败，无法生成任务表。\n"
                "请确认机器人能访问该文档/文件，并且应用已开通读取权限。\n\n"
                f"错误信息：{exc}"
            )
        if content is None:
            return CommandResult(
                "可以。请把飞书文档链接、Word 或 PDF 发给我，"
                "我会帮你拆成任务表，并同步到飞书任务管理。\n\n"
                "你也可以直接说：\n"
                "“根据这份资料做项目计划”\n"
                "“帮我拆一下开发任务”"
            )

        if not content.strip():
            return CommandResult("这份资料里暂时没有读到可拆解的内容，可以换一份更完整的需求文档或项目说明。")

        project_name = extract_project_name(command_text) or "项目"
        project_id = normalize_project_id(project_name)
        try:
            result = await create_task_table_from_doc(
                project_name,
                content,
                chat_id=chat_id,
                project_id=project_id,
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
            _extract_feishu_origin(command_text),
            result["app_token"],
            result["table_id"],
        )
        remember_generated_task_table(
            chat_id=chat_id,
            sender_open_id=sender_open_id,
            result=result,
            link=link,
            project_name=project_name,
        )
        remember_project_context(
            chat_id=chat_id,
            sender_open_id=sender_open_id,
            project_name=project_name,
            project_id=project_id,
            app_token=result.get("app_token", ""),
            table_id=result.get("table_id", ""),
            link=link,
        )
        log_audit(
            "create_task_table",
            actor_open_id=sender_open_id,
            chat_id=chat_id,
            details={
                "project_name": project_name,
                "app_token": result.get("app_token", ""),
                "table_id": result.get("table_id", ""),
                "task_count": result.get("task_count", 0),
                "task_created_count": result.get("task_created_count", 0),
                "task_failed_count": result.get("task_failed_count", 0),
                "link": link,
            },
        )
        return CommandResult(format_task_table_result(result, link, project_name=project_name))

    if _is_work_log_table_intent(command_text):
        remember_pending_action(
            chat_id,
            sender_open_id,
            "create_work_log_table",
            {"origin": _extract_feishu_origin(command_text)},
        )
        return CommandResult(
            "好的，我可以帮你创建一个工作日志多维表格。\n\n"
            "我建议先包含这些字段：\n"
            "1. 日期\n"
            "2. 工作事项\n"
            "3. 完成进度\n"
            "4. 备注/困难点\n"
            "5. 负责人\n\n"
            "如果可以，你回复“是”，我就马上创建真实表格并发你链接。"
        )

    if command_text.startswith("/生成项目表"):
        project_name = command_text.replace("/生成项目表", "", 1).strip() or "新项目"
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

    if not command_text.startswith("/"):
        return await _ask(command_text or normalized, chat_id=chat_id, sender_open_id=sender_open_id)

    if intent.name == "unknown_command":
        return CommandResult("我还不支持这种说法。你可以直接告诉我想做什么，比如：帮我把这份文档拆成任务。")

    return CommandResult("我还没理解你的意思。你可以直接说想完成什么，比如：帮我把这份文档拆成任务。")

