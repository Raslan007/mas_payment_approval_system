from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import time

from flask import current_app, url_for
from sqlalchemy import and_, case, func, inspect
from sqlalchemy.exc import OperationalError
from werkzeug.routing import BuildError

from extensions import db
from models import PaymentRequest
from .dashboard_helpers import resolve_sla_thresholds

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

ACTION_ENDPOINTS: dict[str, str] = {
    "admin": "payments.list_all",
    "engineering_manager": "payments.eng_review",
    "project_manager": "payments.pm_review",
    "finance": "payments.list_finance_review",
}
READY_ENDPOINT = "payments.finance_eng_approved"
DEFAULT_LISTING_ENDPOINT = "payments.index"
_CACHE_TTL_SECONDS = 30
_STATUS_CACHE: dict[tuple[int | None, str, bool], dict[str, Any]] = {}


def _payment_table_exists() -> bool:
    try:
        inspector = inspect(db.engine)
        return inspector.has_table(PaymentRequest.__tablename__)
    except Exception:
        return False


def _safe_url(endpoint: str | None) -> str | None:
    if not endpoint:
        return None

    try:
        return url_for(endpoint)
    except (BuildError, RuntimeError, OperationalError):
        return None


def _count_for_statuses(base_query, statuses: set[str]) -> int:
    if not statuses:
        return 0

    return (
        base_query.filter(PaymentRequest.status.in_(statuses))
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )


def _compute_overdue_count(base_query) -> int:
    sla_thresholds = resolve_sla_thresholds(current_app.config)
    timestamp_column = func.coalesce(
        PaymentRequest.updated_at, PaymentRequest.created_at, func.now()
    )

    overdue_expr = None
    now = datetime.utcnow()
    for stage, days in sla_thresholds.items():
        try:
            days_int = int(days)
        except (TypeError, ValueError):
            continue

        if days_int <= 0:
            continue

        cutoff = now - timedelta(days=days_int)
        stage_expr = func.sum(
            case(
                (
                    and_(
                        PaymentRequest.status == stage,
                        timestamp_column < cutoff,
                    ),
                    1,
                ),
                else_=0,
            )
        )
        overdue_expr = stage_expr if overdue_expr is None else overdue_expr + stage_expr

    if overdue_expr is None:
        return 0

    return base_query.with_entities(func.coalesce(overdue_expr, 0)).scalar() or 0


def build_status_chips(
    base_query,
    role_name: str,
    *,
    notifications_count: int = 0,
    include_notifications: bool = True,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Compute dashboard status chips using lightweight aggregates and role-aware scopes.
    """
    if not _payment_table_exists():
        return []

    cache_key = (user_id, role_name or "", include_notifications)
    now_ts = time.time()
    cached = _STATUS_CACHE.get(cache_key)
    if cached and now_ts - cached.get("ts", 0) < _CACHE_TTL_SECONDS:
        return cached["chips"]

    working_query = base_query.order_by(None)

    action_required_statuses = ACTION_REQUIRED_STATUSES.get(role_name, set())
    action_required_count = _count_for_statuses(working_query, action_required_statuses)

    ready_for_payment_count = 0
    if role_name in READY_FOR_PAYMENT_ROLES:
        ready_for_payment_count = _count_for_statuses(
            working_query, {STATUS_READY_FOR_PAYMENT}
        )

    overdue_count = _compute_overdue_count(working_query)

    action_endpoint = _safe_url(ACTION_ENDPOINTS.get(role_name) or DEFAULT_LISTING_ENDPOINT)
    ready_endpoint = _safe_url(READY_ENDPOINT) if ready_for_payment_count else None

    chips: list[dict[str, Any]] = [
        {
            "key": "action_required",
            "label": "مطلوب إجراء منك",
            "count": action_required_count,
            "url": action_endpoint,
        },
        {
            "key": "overdue",
            "label": "متأخر",
            "count": overdue_count,
            "url": _safe_url(DEFAULT_LISTING_ENDPOINT),
        },
    ]

    if role_name in READY_FOR_PAYMENT_ROLES:
        chips.append(
            {
                "key": "ready_for_payment",
                "label": "جاهز للصرف",
                "count": ready_for_payment_count,
                "url": ready_endpoint,
            }
        )

    if include_notifications:
        chips.append(
            {
                "key": "notifications",
                "label": "غير مقروء",
                "count": notifications_count,
                "url": _safe_url("notifications.list_notifications"),
            }
        )

    _STATUS_CACHE[cache_key] = {"ts": now_ts, "chips": chips}
    return chips
