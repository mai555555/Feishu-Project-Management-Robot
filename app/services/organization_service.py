import re
import sqlite3
import time
from pathlib import Path

from app.config import settings

ROLE_LABELS = {
    "admin": "管理员",
    "manager": "项目经理",
    "employee": "普通员工",
}
ROLE_ALIASES = {
    "管理员": "admin",
    "管理人员": "admin",
    "公司管理者": "admin",
    "项目经理": "manager",
    "项目负责人": "manager",
    "主管": "manager",
    "负责人": "manager",
    "普通员工": "employee",
    "员工": "employee",
    "成员": "employee",
}
ROLE_PERMISSIONS = {
    "admin": {"manage_org", "bind_assignee", "set_project_owner", "view_org"},
    "manager": {"view_org"},
    "employee": {"view_org"},
}


def _db_path() -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "organization.sqlite3"


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS org_members (
            open_id TEXT PRIMARY KEY,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'employee',
            department TEXT,
            title TEXT,
            user_id TEXT,
            union_id TEXT,
            email TEXT,
            mobile TEXT,
            active INTEGER DEFAULT 1,
            source TEXT,
            department_ids TEXT,
            is_department_leader INTEGER DEFAULT 0,
            managed_department_ids TEXT,
            updated_by_open_id TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    _ensure_column(conn, "org_members", "user_id", "TEXT")
    _ensure_column(conn, "org_members", "union_id", "TEXT")
    _ensure_column(conn, "org_members", "email", "TEXT")
    _ensure_column(conn, "org_members", "mobile", "TEXT")
    _ensure_column(conn, "org_members", "active", "INTEGER DEFAULT 1")
    _ensure_column(conn, "org_members", "source", "TEXT")
    _ensure_column(conn, "org_members", "department_ids", "TEXT")
    _ensure_column(conn, "org_members", "is_department_leader", "INTEGER DEFAULT 0")
    _ensure_column(conn, "org_members", "managed_department_ids", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_org_members_role ON org_members(role)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_org_members_user_id ON org_members(user_id)")
    return conn


def normalize_role(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", "", value)
    if compact in ROLE_ALIASES:
        return ROLE_ALIASES[compact]
    if compact in ROLE_LABELS:
        return compact
    return None


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "employee", "普通员工")


def has_any_admin() -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM org_members WHERE role = 'admin' LIMIT 1").fetchone()
    return bool(row)


def upsert_member(
    open_id: str,
    display_name: str = "",
    *,
    role: str = "employee",
    department: str = "",
    title: str = "",
    user_id: str = "",
    union_id: str = "",
    email: str = "",
    mobile: str = "",
    active: bool = True,
    source: str = "manual",
    department_ids: str = "",
    is_department_leader: bool = False,
    managed_department_ids: str = "",
    updated_by_open_id: str | None = None,
    preserve_existing_role: bool = False,
) -> dict[str, str]:
    if not open_id:
        raise ValueError("没有拿到成员 open_id，请在飞书里 @ 具体同事。")
    normalized_role = normalize_role(role) or "employee"
    existing = get_member(open_id)
    if preserve_existing_role and existing and existing.get("role") == "admin":
        normalized_role = "admin"
    elif preserve_existing_role and existing and existing.get("role") == "manager":
        normalized_role = "manager"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO org_members (
                open_id, display_name, role, department, title, user_id, union_id,
                email, mobile, active, source, department_ids, is_department_leader,
                managed_department_ids, updated_by_open_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(open_id) DO UPDATE SET
                display_name = COALESCE(NULLIF(excluded.display_name, ''), org_members.display_name),
                role = excluded.role,
                department = COALESCE(NULLIF(excluded.department, ''), org_members.department),
                title = COALESCE(NULLIF(excluded.title, ''), org_members.title),
                user_id = COALESCE(NULLIF(excluded.user_id, ''), org_members.user_id),
                union_id = COALESCE(NULLIF(excluded.union_id, ''), org_members.union_id),
                email = COALESCE(NULLIF(excluded.email, ''), org_members.email),
                mobile = COALESCE(NULLIF(excluded.mobile, ''), org_members.mobile),
                active = excluded.active,
                source = COALESCE(NULLIF(excluded.source, ''), org_members.source),
                department_ids = COALESCE(NULLIF(excluded.department_ids, ''), org_members.department_ids),
                is_department_leader = CASE
                    WHEN excluded.is_department_leader = 1 THEN 1
                    ELSE org_members.is_department_leader
                END,
                managed_department_ids = COALESCE(NULLIF(excluded.managed_department_ids, ''), org_members.managed_department_ids),
                updated_by_open_id = excluded.updated_by_open_id,
                updated_at = excluded.updated_at
            """,
            (
                open_id,
                display_name.strip(),
                normalized_role,
                department.strip(),
                title.strip(),
                user_id.strip(),
                union_id.strip(),
                email.strip(),
                mobile.strip(),
                1 if active else 0,
                source.strip(),
                department_ids.strip(),
                1 if is_department_leader else 0,
                managed_department_ids.strip(),
                updated_by_open_id,
                time.time(),
            ),
        )
    return {
        "open_id": open_id,
        "display_name": display_name.strip(),
        "role": normalized_role,
        "role_label": role_label(normalized_role),
    }


def count_members() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM org_members WHERE active = 1").fetchone()
    return int(row[0] or 0) if row else 0

def get_member(open_id: str | None) -> dict[str, str] | None:
    if not open_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT open_id, display_name, role, department, title, user_id, union_id,
                   email, mobile, active, source, department_ids, is_department_leader,
                   managed_department_ids
            FROM org_members WHERE open_id = ?
            """,
            (open_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "open_id": str(row[0]),
        "display_name": str(row[1] or ""),
        "role": str(row[2] or "employee"),
        "role_label": role_label(str(row[2] or "employee")),
        "department": str(row[3] or ""),
        "title": str(row[4] or ""),
        "user_id": str(row[5] or ""),
        "union_id": str(row[6] or ""),
        "email": str(row[7] or ""),
        "mobile": str(row[8] or ""),
        "active": str(row[9] if row[9] is not None else 1),
        "source": str(row[10] or ""),
        "department_ids": str(row[11] or ""),
        "is_department_leader": str(row[12] or 0),
        "managed_department_ids": str(row[13] or ""),
    }


def member_role(open_id: str | None) -> str:
    member = get_member(open_id)
    return member["role"] if member else "employee"


def has_permission(open_id: str | None, permission: str) -> bool:
    role = member_role(open_id)
    return permission in ROLE_PERMISSIONS.get(role, set())


def can_bootstrap_admin(open_id: str | None) -> bool:
    if not open_id:
        return False
    allowed = {
        item.strip()
        for item in settings.bootstrap_admin_open_ids.split(",")
        if item.strip()
    }
    return open_id in allowed


def can_manage_org(open_id: str | None) -> bool:
    if not has_any_admin():
        return can_bootstrap_admin(open_id)
    return has_permission(open_id, "manage_org")


def can_bind_assignee(open_id: str | None) -> bool:
    if not has_any_admin():
        return False
    return has_permission(open_id, "bind_assignee")


def can_set_project_owner(open_id: str | None) -> bool:
    if not has_any_admin():
        return False
    return has_permission(open_id, "set_project_owner")


def permission_denied_text(action: str) -> str:
    return f"这个操作需要公司管理员权限：{action}。请让公司管理员来处理。"


def _safe_member_name(open_id: str, display_name: str | None) -> str:
    if display_name:
        return display_name
    if open_id:
        return f"成员 {open_id[-8:]}"
    return "未记录姓名"


def _safe_department_text(department: str | None) -> str:
    value = (department or "").strip()
    if not value or value.startswith("od-") or value.startswith("["):
        return ""
    return value


def list_members(role: str | None = None, *, limit: int = 50) -> list[str]:
    normalized_role = normalize_role(role)
    query = "SELECT open_id, display_name, role, department, title FROM org_members WHERE active = 1"
    params: list[object] = []
    if normalized_role:
        query += " AND role = ?"
        params.append(normalized_role)
    query += " ORDER BY role ASC, updated_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for open_id, display_name, row_role, department, title in rows:
        parts = [_safe_member_name(str(open_id or ""), str(display_name or "")), role_label(str(row_role or "employee"))]
        department_text = _safe_department_text(str(department or ""))
        if department_text:
            parts.append(department_text)
        if title:
            parts.append(str(title))
        result.append(" - ".join(parts))
    return result

def describe_member(open_id: str | None, fallback_name: str = "") -> str:
    member = get_member(open_id)
    if not member:
        name = fallback_name or "这个同事"
        return f"我还没有记录{name}的组织角色。"
    name = member.get("display_name") or fallback_name or "这个同事"
    extra = []
    if member.get("department"):
        extra.append(f"部门：{member['department']}")
    if member.get("title"):
        extra.append(f"岗位：{member['title']}")
    if member.get("is_department_leader") == "1":
        extra.append("公司通讯录标记为部门负责人")
    suffix = "，" + "，".join(extra) if extra else ""
    return f"{name} 当前角色是：{member['role_label']}{suffix}。"
