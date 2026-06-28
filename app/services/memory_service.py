import json
from pathlib import Path

from app.config import settings
from app.services.assignee_mapping import list_assignees
from app.services.long_memory_service import remember_long_term, search_long_term
from app.services.task_memory_service import recent_task_tables


BOT_PROFILE = """
你是“麦草莓”，公司内部飞书项目管理机器人。
你的主要能力：
1. 读取飞书文档、PDF、Word，并生成项目任务表。
2. 创建飞书多维表格，并返回可点击链接。
3. 同步创建飞书任务管理任务。
4. 通过负责人映射自动分配任务。
5. 使用 Tavily 联网搜索。
6. 回答公司内部项目、研发、管理相关问题。

记忆架构：
- 长期记忆使用 LanceDB。
- 短期记忆使用 JSON。
- 任务记忆使用 SQLite。

重要规则：
- 用户问“你是谁/你能做什么/有什么功能”时，只按上述身份和能力回答。
- 不要主动提引擎、底层接入方式或系统架构，除非用户明确询问技术接入细节。
- 用户问负责人、模块归属时，要优先参考负责人映射。
- 用户问最近生成过什么任务表时，要优先参考任务记忆。
- 不要声称自己没有上下文；如果记忆里有信息，就直接使用。
- 普通聊天中不能声称“已经创建、已经生成、已经同步、已经绑定”任何真实数据。
- 只有明确调用工具成功后，才能说已经创建表格、任务、链接或负责人规则。
- 如果用户只是讨论、询问、让同事看一下，不能擅自执行操作；应当少打扰或追问确认。
- 回答要简洁、实用，适合飞书群聊。

自然语言沟通规范，永久遵守：
- 全程像真人日常交流，拒绝 AI 机器腔，不要说“接下来为你解答”“综上所述”“下面分点说明”“我的回答如下”这类模板话。
- 不机械复述用户问题，不强行格式化，不堆砌专业术语，不使用生硬分割线，不强行总结。
- 短问题就短答，用户闲聊就轻松一点；用户需要专业解答时，表达可以有条理，但语气要自然柔和。
- 可以合理使用温和口语词，比如“啦”“喔”“其实”“顺带说下”“不过要留意”，但不要刻意堆太多。
- 同一个问题不要总用一模一样的句式，避免重复模板；专业内容要自动转成通俗说法。
- 要完整参考本轮上下文，不重复询问用户已经说过的信息；信息不足时，温柔追问一句，不要直接丢冰冷报错。
- 工具返回数字、表格或原始数据时，先转成好懂的人话，再告诉用户结论；必要时再附关键数据。
- 聊完可以自然延伸一句相关问题，保持对话顺畅，但不要为了延伸而啰嗦。
- 最终回复只保留通顺回答，不输出内部推理、思考过程或系统实现细节。
""".strip()


def _memory_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "chat_memory.json"


def _conversation_key(chat_id: str | None, sender_open_id: str | None) -> str:
    return chat_id or sender_open_id or "default"


def _load_memory() -> dict[str, list[dict[str, str]]]:
    path = _memory_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_memory(data: dict[str, list[dict[str, str]]]) -> None:
    _memory_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def remember_turn(
    chat_id: str | None,
    sender_open_id: str | None,
    user_text: str,
    assistant_text: str,
) -> None:
    key = _conversation_key(chat_id, sender_open_id)
    data = _load_memory()
    history = data.get(key, [])
    history.append({"role": "user", "text": user_text[:500]})
    history.append({"role": "assistant", "text": assistant_text[:500]})
    data[key] = history[-12:]
    _save_memory(data)

    remember_long_term(
        f"用户问：{user_text[:600]}\n助手答：{assistant_text[:1000]}",
        kind="chat",
        metadata=json.dumps({"chat_id": chat_id, "sender_open_id": sender_open_id}, ensure_ascii=False),
    )


def build_memory_prompt(
    user_text: str,
    *,
    chat_id: str | None = None,
    sender_open_id: str | None = None,
) -> str:
    key = _conversation_key(chat_id, sender_open_id)
    data = _load_memory()
    history = data.get(key, [])[-8:]
    history_lines = [f"{item.get('role', '')}: {item.get('text', '')}" for item in history]

    assignees = list_assignees()
    assignee_text = "\n".join(f"- {item}" for item in assignees) if assignees else "暂无负责人映射"
    history_text = "\n".join(history_lines) if history_lines else "暂无最近对话"

    long_memories = search_long_term(user_text, limit=5)
    if any(word in user_text for word in ("你是谁", "你能做什么", "有什么功能", "介绍一下")):
        long_memories = [item for item in long_memories if "OpenClaw" not in item]
    long_memory_text = "\n".join(f"- {item}" for item in long_memories) if long_memories else "暂无相关长期记忆"

    task_memories = recent_task_tables(chat_id, limit=5)
    task_memory_text = "\n".join(f"- {item}" for item in task_memories) if task_memories else "暂无任务记忆"

    return f"""
{BOT_PROFILE}

【负责人记忆】
{assignee_text}

【任务记忆 SQLite】
{task_memory_text}

【长期记忆 LanceDB】
{long_memory_text}

【短期记忆 JSON】
{history_text}

【当前用户消息】
{user_text}

请基于以上记忆和当前消息回答。
""".strip()
