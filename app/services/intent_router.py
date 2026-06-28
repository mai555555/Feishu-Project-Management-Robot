from dataclasses import dataclass


@dataclass(frozen=True)
class IntentResult:
    name: str
    confidence: float
    reason: str = ""
    requires_confirmation: bool = False


ACTION_WORDS = (
    "帮我",
    "请",
    "创建",
    "新建",
    "生成",
    "做一个",
    "建一个",
    "弄一个",
    "拆",
    "整理",
    "总结",
    "搜索",
    "查一下",
    "联网查",
    "绑定",
    "设置",
    "设为",
    "改为",
    "改成",
    "换成",
    "分配",
    "交给",
    "同步",
    "查询",
    "列出",
)

ASSIGNEE_CONFIG_PATTERNS = (
    "负责人是",
    "负责人为",
    "负责人设为",
    "负责人设置为",
    "设置负责人",
    "设为负责人",
    "绑定负责人",
    "分配给",
    "交给",
    "由",
)

ASSIGNEE_QUESTION_WORDS = (
    "谁负责",
    "负责吗",
    "是不是负责",
    "谁来负责",
    "谁跟",
    "谁看",
    "怎么负责",
)
QUESTION_OR_EXPLAIN_WORDS = ("?", "？", "吗", "呢", "怎么", "为什么", "如何", "什么", "问题")

TABLE_ACTION_WORDS = ("创建", "新建", "生成", "做一个", "建一个", "弄一个")
TABLE_WORDS = ("表格", "多维表格", "记录表", "日志表", "日报表")
WORK_LOG_WORDS = ("工作日志", "工作日记", "工作内容", "每天记录", "每日记录", "日报", "日记录")
TASK_TABLE_WORDS = ("任务表", "拆任务", "拆一下任务", "拆解任务", "拆成任务", "拆成开发任务", "项目计划", "任务管理表", "项目管理表")
SEARCH_WORDS = (
    "搜索",
    "搜索一下",
    "联网搜索",
    "联网搜索一下",
    "查一下",
    "联网查",
    "联网查一下",
    "帮我搜索",
    "帮我搜索一下",
    "帮我联网查",
    "帮我联网查一下",
)
TASK_QUERY_WORDS = ("未分配", "没分配", "待分配", "待定", "负责什么", "哪些任务", "有什么任务", "任务有哪些", "负责的任务", "谁的任务", "列出任务")


def has_action_word(text: str) -> bool:
    normalized = text.strip().lower()
    return any(word.lower() in normalized for word in ACTION_WORDS)


def is_assignee_binding_intent(text: str, *, has_target_mention: bool) -> bool:
    normalized = text.strip()
    if normalized.startswith(("/绑定负责人", "绑定负责人", "绑定")):
        return True
    if any(word in normalized for word in ASSIGNEE_QUESTION_WORDS):
        return False
    if any(word in normalized for word in QUESTION_OR_EXPLAIN_WORDS):
        return False
    if "负责人" in normalized and any(word in normalized for word in ("是", "为", "设为", "设置", "改为", "改成", "换成")):
        return True
    if not has_target_mention:
        return False
    if any(word in normalized for word in ("分配给", "交给", "绑定给")):
        return True
    if "由" in normalized and "负责" in normalized:
        return True
    return False


def classify_intent(text: str, *, has_target_mention: bool = False) -> IntentResult:
    normalized = text.strip()
    lower = normalized.lower()

    if normalized in {"/帮助", "帮助", "/help"}:
        return IntentResult("help", 1.0)
    if normalized.startswith(("/问", "/ai")):
        return IntentResult("chat", 1.0)
    if any(lower.startswith(word.lower()) for word in SEARCH_WORDS) or normalized.startswith("/搜索"):
        return IntentResult("search", 0.95)
    if "负责人" in normalized and any(word in normalized for word in ("吗", "？", "?", "是不是", "是否")):
        return IntentResult("assignee_question", 0.9, "responsibility question")
    if "负责" in normalized and any(word in normalized for word in ("吗", "？", "?", "是不是", "是否")):
        return IntentResult("assignee_question", 0.85, "responsibility question")
    if has_target_mention and any(word in normalized for word in ASSIGNEE_QUESTION_WORDS):
        return IntentResult("assignee_question", 0.9, "asking whether someone is responsible")
    if is_assignee_binding_intent(normalized, has_target_mention=has_target_mention):
        return IntentResult("assignee_binding", 0.95, requires_confirmation=True)
    if any(word in normalized for word in TASK_QUERY_WORDS):
        return IntentResult("task_query", 0.9, "task query")
    if any(word in lower for word in TASK_TABLE_WORDS):
        return IntentResult("task_table", 0.9, requires_confirmation=False)
    if (
        any(word in lower for word in TABLE_WORDS)
        and any(word in lower for word in WORK_LOG_WORDS)
        and any(word in lower for word in TABLE_ACTION_WORDS)
    ):
        return IntentResult("work_log_table", 0.9, requires_confirmation=True)
    if normalized.startswith("/"):
        return IntentResult("unknown_command", 0.8)
    if not has_action_word(normalized):
        return IntentResult("chat", 0.85, "no explicit action word")
    return IntentResult("chat", 0.55, "fallback")
