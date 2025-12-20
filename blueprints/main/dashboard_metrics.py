from __future__ import annotations

from typing import Any
import time

from flask import current_app, url_for
from sqlalchemy import func, inspect
from sqlalchemy.exc import OperationalError
from werkzeug.routing import BuildError

from extensions import db
from models import PaymentRequest
from blueprints.payments.inbox_queries import (
    ACTION_REQUIRED_STATUSES,
    READY_FOR_PAYMENT_ROLES,
    build_action_required_query,
    build_overdue_query,
    build_ready_for_payment_query,
)

ACTION_ENDPOINT = "payments.inbox_action_required"
OVERDUE_ENDPOINT = "payments.inbox_overdue"
READY_ENDPOINT = "payments.inbox_ready_for_payment"
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


def _count_from_query(q) -> int:
    return (
        q.order_by(None)
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )


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

    action_required_q = build_action_required_query(working_query, role_name)
    action_required_count = _count_from_query(action_required_q)

    ready_for_payment_count = 0
    if role_name in READY_FOR_PAYMENT_ROLES:
        ready_for_payment_count = _count_from_query(
            build_ready_for_payment_query(working_query)
        )

    overdue_count = _count_from_query(
        build_overdue_query(
            working_query,
            config=current_app.config,
        )
    )

    action_endpoint = (
        _safe_url(ACTION_ENDPOINT)
        if ACTION_REQUIRED_STATUSES.get(role_name)
        else None
    )
    ready_endpoint = _safe_url(READY_ENDPOINT) if ready_for_payment_count else None
    overdue_endpoint = _safe_url(OVERDUE_ENDPOINT)

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
            "url": overdue_endpoint or _safe_url(DEFAULT_LISTING_ENDPOINT),
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
