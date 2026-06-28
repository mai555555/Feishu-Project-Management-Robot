from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.feishu_client import feishu_client
from app.services.organization_service import count_members, upsert_member


def _display_name(user: dict[str, Any]) -> str:
    return str(
        user.get("name")
        or user.get("en_name")
        or user.get("nickname")
        or user.get("email")
        or user.get("mobile")
        or ""
    ).strip()


def _user_open_id(user: dict[str, Any]) -> str:
    return str(
        user.get("open_id")
        or user.get("openId")
        or user.get("user_id")
        or user.get("userId")
        or ""
    ).strip()


def _department_name(department: dict[str, Any], fallback: str) -> str:
    return str(department.get("name") or department.get("i18n_name", {}).get("zh_cn") or fallback or "").strip()


def _department_id(department: dict[str, Any]) -> str:
    return str(
        department.get("open_department_id")
        or department.get("department_id")
        or department.get("departmentId")
        or ""
    ).strip()


def _title(user: dict[str, Any]) -> str:
    return str(user.get("job_title") or user.get("jobTitle") or user.get("position") or "").strip()


def _is_active(user: dict[str, Any]) -> bool:
    status = user.get("status")
    if isinstance(status, dict):
        if status.get("is_frozen") or status.get("is_resigned") or status.get("is_activated") is False:
            return False
    if user.get("is_resigned") is True:
        return False
    return True


def _leader_open_ids(department: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    leader_user_id = department.get("leader_user_id") or department.get("leaderUserId")
    if leader_user_id:
        result.add(str(leader_user_id))
    leaders = department.get("leaders") or []
    if isinstance(leaders, list):
        for leader in leaders:
            if not isinstance(leader, dict):
                continue
            leader_id = leader.get("leaderID") or leader.get("leader_id") or leader.get("leaderUserId")
            if leader_id:
                result.add(str(leader_id))
    return result


async def sync_feishu_contacts(*, root_department_id: str | None = None, updated_by_open_id: str | None = None) -> dict[str, object]:
    root_id = root_department_id or settings.feishu_root_department_id or "0"
    visited_departments: set[str] = set()
    synced_users: dict[str, str] = {}
    failed_departments: list[str] = []
    missing_name_count = 0
    department_leaders: dict[str, set[str]] = {}
    department_names: dict[str, str] = {root_id: ""}

    async def visit(department_id: str, department_name: str = "") -> None:
        if department_id in visited_departments:
            return
        visited_departments.add(department_id)
        department_names[department_id] = department_name

        try:
            children = await feishu_client.list_child_departments(department_id)
        except Exception as exc:
            failed_departments.append(f"{department_name or department_id} 子部门: {exc}")
            children = []

        for child in children:
            child_id = _department_id(child)
            if not child_id:
                continue
            child_name = _department_name(child, child_id)
            department_names[child_id] = child_name
            leaders = _leader_open_ids(child)
            if leaders:
                department_leaders.setdefault(child_id, set()).update(leaders)
            await visit(child_id, child_name)

        try:
            users = await feishu_client.list_department_users(department_id)
        except Exception as exc:
            failed_departments.append(f"{department_name or department_id}: {exc}")
            users = []

        nonlocal missing_name_count
        for user in users:
            open_id = _user_open_id(user)
            if not open_id:
                continue
            name = _display_name(user)
            if not name:
                missing_name_count += 1
            department_ids = [str(item) for item in (user.get("department_ids") or []) if item]
            if not department_ids:
                department_ids = [department_id]
            primary_department_id = department_id
            orders = user.get("orders") or []
            if isinstance(orders, list):
                for order in orders:
                    if isinstance(order, dict) and order.get("is_primary_dept") and order.get("department_id"):
                        primary_department_id = str(order["department_id"])
                        break
            department_text = department_names.get(primary_department_id) or department_names.get(department_id, "")
            managed_department_ids = sorted(
                dept_id for dept_id, leaders in department_leaders.items() if open_id in leaders
            )
            is_leader = bool(managed_department_ids)
            upsert_member(
                open_id,
                name,
                role="manager" if is_leader else "employee",
                department=department_text,
                title=_title(user),
                user_id=str(user.get("user_id") or ""),
                union_id=str(user.get("union_id") or ""),
                email=str(user.get("email") or ""),
                mobile=str(user.get("mobile") or ""),
                active=_is_active(user),
                source="feishu_contact",
                department_ids=json.dumps(department_ids, ensure_ascii=False),
                is_department_leader=is_leader,
                managed_department_ids=json.dumps(managed_department_ids, ensure_ascii=False),
                updated_by_open_id=updated_by_open_id,
                preserve_existing_role=True,
            )
            synced_users[open_id] = name or open_id

    await visit(root_id, "")
    leader_count = len({leader for leaders in department_leaders.values() for leader in leaders})
    return {
        "departments": len(visited_departments),
        "users": len(synced_users),
        "department_leaders": leader_count,
        "total_members": count_members(),
        "failed_departments": failed_departments[:5],
        "missing_name_count": missing_name_count,
    }
