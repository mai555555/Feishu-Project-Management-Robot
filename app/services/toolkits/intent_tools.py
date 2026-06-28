TASK_TABLE_INTENT_KEYWORDS = (
    "生成任务表",
    "任务表",
    "拆任务",
    "拆一下任务",
    "拆解任务",
    "拆成任务",
    "拆成开发任务",
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


def is_task_table_intent(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.startswith("/生成任务表"):
        return True
    if normalized.startswith("/"):
        return False
    return any(keyword.lower() in normalized for keyword in TASK_TABLE_INTENT_KEYWORDS)


def is_work_log_table_intent(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized.startswith("/创建工作日志表") or normalized.startswith("/生成工作日志表"):
        return True
    if normalized.startswith("/"):
        return False

    table_words = ("表格", "多维表格", "记录表", "日志表", "日报表")
    work_log_words = (
        "工作日志",
        "工作日记",
        "工作内容",
        "每天记录",
        "每日记录",
        "日报",
        "日记录",
    )
    create_words = ("创建", "新建", "生成", "做一个", "建一个", "弄一个")
    return (
        any(word in normalized for word in table_words)
        and any(word in normalized for word in work_log_words)
        and any(word in normalized for word in create_words)
    )


def is_confirm_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"是", "确认", "可以", "好的", "好", "ok", "yes", "对", "没问题"}


def is_cancel_text(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"否", "不用", "取消", "先不用", "no", "不要"}
