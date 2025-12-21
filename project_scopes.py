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


def _has_user_projects_table() -> bool:
    try:
        inspector = inspect(db.engine)
        return inspector.has_table("user_projects")
    except Exception:
        return False


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


def get_scoped_project_ids(user, *, role_name: str | None = None) -> list[int]:
    """
    Return the list of project IDs the given user can access for the provided role.

    - Uses the ``user_projects`` link table when available.
    - Falls back to the user's primary ``project_id`` when no linked projects are
      found (supports legacy single-project setups).
    - Currently supports project-based scoping for project managers and
      engineers only; other roles receive an empty list.
    """

    if not getattr(user, "id", None):
        return []

    resolved_role = role_name or (user.role.name if getattr(user, "role", None) else None)
    if resolved_role not in {"project_manager", "engineer"}:
        return []

    project_ids: list[int] = []

    if _has_user_projects_table():
        try:
            rows = (
                db.session.query(user_projects.c.project_id)
                .filter(user_projects.c.user_id == user.id)
                .all()
            )
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
