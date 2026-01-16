"""Shared helpers for resolving project access per user/role.

These helpers centralize the logic for reading the ``user_projects`` link table
and falling back to legacy single-project assignments to keep older data
working. They are intentionally lightweight and free of Flask globals so they
can be reused across blueprints and tests.
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import inspect

from extensions import db
from models import user_projects

# Cache a reflected version of ``user_projects`` when a role column exists so we
# can respect per-role assignments without forcing a schema change when the
# column is absent.
_reflected_user_projects_with_role = None

ALLOWED_SCOPED_ROLES = {"project_manager", "engineer", "project_engineer", "procurement"}


def _has_user_projects_table() -> bool:
    try:
        inspector = inspect(db.engine)
        return inspector.has_table("user_projects")
    except Exception:
        return False


def _user_projects_table_with_role():
    global _reflected_user_projects_with_role

    if _reflected_user_projects_with_role is not None:
        return _reflected_user_projects_with_role

    try:
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("user_projects")}
    except Exception:
        return None

    if "scoped_role" not in columns:
        return None

    try:
        _reflected_user_projects_with_role = db.Table(
            "user_projects",
            db.MetaData(),
            autoload_with=db.engine,
        )
    except Exception:
        _reflected_user_projects_with_role = None

    return _reflected_user_projects_with_role


def _dedupe_ints(values: Iterable[int | None]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _normalize_role(role_name: str | None) -> str | None:
    if role_name == "project_engineer":
        return "engineer"
    return role_name


def _current_user_projects_table():
    """
    Return (table, has_role_column) for the ``user_projects`` link table.

    When a role column exists, a reflected table with the ``scoped_role`` column
    is returned; otherwise the declarative ``user_projects`` table is used.
    """

    if not _has_user_projects_table():
        return None, False

    table_with_role = _user_projects_table_with_role()
    if table_with_role is not None and "scoped_role" in table_with_role.c:
        return table_with_role, True

    return user_projects, False


def get_scoped_project_ids(user, *, role_name: str | None = None) -> list[int]:
    """
    Return the list of project IDs the given user can access for the provided role.

    - Uses the ``user_projects`` link table when available.
    - Falls back to the user's primary ``project_id`` when no linked projects are
      found (supports legacy single-project setups).
    - Currently supports project-based scoping for project managers,
      procurement officers, and engineers (including the project_engineer
      alias) only; other roles receive an empty list.
    """

    if not getattr(user, "id", None):
        return []

    resolved_role = _normalize_role(role_name or (user.role.name if getattr(user, "role", None) else None))
    if resolved_role not in {"project_manager", "engineer", "procurement"}:
        return []

    project_ids: list[int] = []

    table, has_role_column = _current_user_projects_table()
    if table is not None:
        try:
            query = db.session.query(table.c.project_id).filter(table.c.user_id == user.id)
            if has_role_column and resolved_role:
                role_query = query.filter(table.c.scoped_role == resolved_role)
                role_rows = role_query.all()
                project_ids = [row.project_id for row in role_rows if row.project_id]
                if not project_ids:
                    null_rows = query.filter(table.c.scoped_role.is_(None)).all()
                    project_ids = [row.project_id for row in null_rows if row.project_id]
            else:
                rows = query.all()
                project_ids = [row.project_id for row in rows if row.project_id]
        except Exception:
            project_ids = []

    if not project_ids:
        fallback_project_id = getattr(user, "project_id", None)
        if fallback_project_id:
            project_ids = [fallback_project_id]

    return _dedupe_ints(project_ids)


def project_access_allowed(user, project_id: int | None, *, role_name: str | None = None) -> bool:
    if project_id is None:
        return False

    scoped_ids = get_scoped_project_ids(user, role_name=role_name)
    if not scoped_ids:
        return False

    return project_id in scoped_ids
