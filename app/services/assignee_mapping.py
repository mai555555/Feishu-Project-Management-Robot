import json
import re
from pathlib import Path

from app.config import settings


ROLE_KEYWORDS = {
    "前端": ["前端", "ui", "页面", "界面", "首页", "表现层", "小程序", "组件", "样式", "交互"],
    "后端": ["后端", "接口", "服务端", "api", "数据库", "数据表", "云函数", "存储", "权限"],
    "产品": ["产品", "需求", "原型", "规划", "评审", "功能设计"],
    "测试": ["测试", "验收", "联调", "bug", "缺陷", "用例"],
    "设计": ["设计", "视觉", "交互", "ui", "原型"],
    "运营": ["运营", "资讯", "公告", "内容", "推广", "活动"],
}


def _mapping_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "assignees.json"


def _normalize_alias(alias: str) -> str:
    return re.sub(r"\s+", "", alias).strip().lower()


def _load_mapping() -> dict[str, str]:
    path = _mapping_path()
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        str(alias): str(open_id)
        for alias, open_id in data.items()
        if str(alias).strip() and str(open_id).strip()
    }


def _save_mapping(mapping: dict[str, str]) -> None:
    path = _mapping_path()
    path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def bind_assignee(alias: str, open_id: str) -> str:
    normalized = _normalize_alias(alias)
    if not normalized:
        raise ValueError("负责人名称不能为空")
    if not open_id:
        raise ValueError("没有拿到当前用户 open_id，请在飞书里发送绑定指令")

    mapping = _load_mapping()
    mapping[normalized] = open_id
    _save_mapping(mapping)
    return normalized


def unbind_assignee(alias: str) -> bool:
    normalized = _normalize_alias(alias)
    mapping = _load_mapping()
    existed = normalized in mapping
    mapping.pop(normalized, None)
    _save_mapping(mapping)
    return existed


def list_assignees() -> list[str]:
    return sorted(_load_mapping().keys())


def resolve_assignee_open_id(owner: str | None) -> str | None:
    if not owner:
        return None

    normalized_owner = _normalize_alias(owner)
    if not normalized_owner:
        return None

    mapping = _load_mapping()
    if normalized_owner in mapping:
        return mapping[normalized_owner]

    for alias, open_id in mapping.items():
        if alias and alias in normalized_owner:
            return open_id

    for alias, open_id in mapping.items():
        if normalized_owner and normalized_owner in alias:
            return open_id

    return None


def resolve_task_assignee(task: dict[str, str]) -> tuple[str | None, str | None]:
    mapping = _load_mapping()
    owner = task.get("owner") or ""

    open_id = resolve_assignee_open_id(owner)
    if open_id:
        return open_id, owner.strip() or None

    search_text = _normalize_alias(
        " ".join(
            [
                task.get("title", ""),
                task.get("description", ""),
                task.get("module", ""),
                owner,
                task.get("notes", ""),
            ]
        )
    )

    for alias, open_id in mapping.items():
        if alias and alias in search_text:
            return open_id, alias

    for role, keywords in ROLE_KEYWORDS.items():
        normalized_role = _normalize_alias(role)
        if normalized_role not in mapping:
            continue
        if any(_normalize_alias(keyword) in search_text for keyword in keywords):
            return mapping[normalized_role], normalized_role

    return None, None
