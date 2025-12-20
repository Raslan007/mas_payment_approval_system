from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from flask import current_app
from sqlalchemy import and_, false, func, inspect, or_

from extensions import db
from models import PaymentRequest, user_projects
from blueprints.main.dashboard_helpers import resolve_sla_thresholds


# حالات العمل الأساسية المستخدمة في لوائح الـ KPI و الـ Inbox
STATUS_PENDING_PM = "pending_pm"
STATUS_PENDING_ENG = "pending_eng"
STATUS_PENDING_FINANCE = "pending_finance"
STATUS_READY_FOR_PAYMENT = "ready_for_payment"

READY_FOR_PAYMENT_ROLES: set[str] = {"finance", "admin", "engineering_manager"}

ACTION_REQUIRED_STATUSES: dict[str, set[str]] = {
    "admin": {
        STATUS_PENDING_PM,
        STATUS_PENDING_ENG,
        STATUS_PENDING_FINANCE,
        STATUS_READY_FOR_PAYMENT,
    },
    "engineering_manager": {STATUS_PENDING_PM, STATUS_PENDING_ENG},
    "project_manager": {STATUS_PENDING_PM},
    "finance": {STATUS_PENDING_FINANCE, STATUS_READY_FOR_PAYMENT},
}


def scoped_inbox_base_query(user) -> tuple:
    """
    Build a base, role-aware query for inbox listings and dashboard KPIs.

    Returns (query, role_name, scoped_project_ids).
    """

    role_name = user.role.name if getattr(user, "role", None) else None
    query = PaymentRequest.query
    scoped_project_ids: list[int] = []

    def _project_ids_from_link_table() -> list[int]:
        try:
            inspector = inspect(db.engine)
            if not inspector.has_table("user_projects"):
                return []
        except Exception:
            return []

        rows = (
            db.session.query(user_projects.c.project_id)
            .filter(user_projects.c.user_id == user.id)
            .all()
        )
        return [row.project_id for row in rows]

    if role_name == "project_manager":
        scoped_project_ids = _project_ids_from_link_table()
        if getattr(user, "project_id", None):
            scoped_project_ids.append(user.project_id)
        scoped_project_ids = list({pid for pid in scoped_project_ids if pid})
        if scoped_project_ids:
            query = query.filter(PaymentRequest.project_id.in_(scoped_project_ids))
        else:
            query = query.filter(false())
    elif role_name == "engineer":
        query = query.filter(PaymentRequest.created_by == getattr(user, "id", None))
    elif role_name == "dc":
        query = query.filter(false())

    return query, role_name, scoped_project_ids


def build_action_required_query(base_query, role_name: str | None):
    statuses = ACTION_REQUIRED_STATUSES.get(role_name, set())
    if not statuses:
        return base_query.filter(false())
    return base_query.filter(PaymentRequest.status.in_(statuses))


def build_ready_for_payment_query(base_query):
    return base_query.filter(PaymentRequest.status == STATUS_READY_FOR_PAYMENT)


def build_overdue_query(
    base_query,
    *,
    now: datetime | None = None,
    config: Mapping[str, object] | None = None,
):
    """
    Construct a query for overdue payments using SLA thresholds.
    """

    clock = now or datetime.utcnow()
    sla_thresholds = resolve_sla_thresholds(config or current_app.config)
    ts_column = func.coalesce(
        PaymentRequest.updated_at, PaymentRequest.created_at, func.now()
    )

    clauses = []
    for stage, days in sla_thresholds.items():
        try:
            stage_days = int(days)
        except (TypeError, ValueError):
            continue

        if stage_days <= 0:
            continue

        cutoff = clock - timedelta(days=stage_days)
        clauses.append(
            and_(PaymentRequest.status == stage, ts_column < cutoff)
        )

    if not clauses:
        return base_query.filter(false())

    return base_query.filter(or_(*clauses))
