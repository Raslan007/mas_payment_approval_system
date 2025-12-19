from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from sqlalchemy import select

from extensions import db
from models import PaymentApproval, PaymentRequest


DEFAULT_SLA_THRESHOLDS: dict[str, int] = {
    "pending_pm": 3,
    "pending_eng": 4,
    "pending_finance": 3,
    "ready_for_payment": 2,
}


def resolve_sla_thresholds(config: Mapping[str, object]) -> dict[str, int]:
    raw_config = config.get("SLA_THRESHOLDS_DAYS", {}) or {}
    merged = {**DEFAULT_SLA_THRESHOLDS}

    if isinstance(raw_config, Mapping):
        for key, value in raw_config.items():
            try:
                int_value = int(value)
            except (TypeError, ValueError):
                continue

            if int_value > 0:
                merged[str(key)] = int_value

    return merged


def compute_overdue_items(
    payments: Iterable[PaymentRequest],
    sla_thresholds: Mapping[str, int],
    *,
    now: datetime | None = None,
):
    clock = now or datetime.utcnow()
    overdue_items: list[dict] = []
    stage_counts: dict[str, int] = defaultdict(int)
    oldest_days = 0

    for payment in payments:
        stage = payment.status
        sla_days = sla_thresholds.get(stage)
        if not sla_days:
            continue

        last_change = payment.updated_at or payment.created_at or clock
        elapsed = clock - last_change
        overdue_delta = elapsed - timedelta(days=sla_days)
        if overdue_delta.total_seconds() <= 0:
            continue

        days_overdue = max(1, int(overdue_delta.days))
        stage_counts[stage] += 1
        oldest_days = max(oldest_days, days_overdue)
        overdue_items.append(
            {
                "payment": payment,
                "days_overdue": days_overdue,
                "sla_days": sla_days,
                "last_change": last_change,
            }
        )

    worst_stage = None
    if stage_counts:
        worst_stage = max(stage_counts.items(), key=lambda pair: (pair[1], pair[0]))[0]

    summary = {
        "total": sum(stage_counts.values()),
        "oldest_days": oldest_days,
        "worst_stage": worst_stage,
        "breakdown": dict(stage_counts),
    }

    overdue_items.sort(key=lambda item: item["days_overdue"], reverse=True)
    return {
        "items": overdue_items,
        "summary": summary,
    }


def _stage_key_from_approval(approval: PaymentApproval) -> str | None:
    if approval.old_status:
        return approval.old_status

    step_mapping = {
        "pm": "pending_pm",
        "eng_manager": "pending_eng",
        "finance": "pending_finance",
    }
    return step_mapping.get(approval.step)


def compute_stage_sla_metrics(payment_ids: list[int]) -> list[dict]:
    if not payment_ids:
        return []

    payment_rows = db.session.execute(
        select(PaymentRequest.id, PaymentRequest.created_at).where(PaymentRequest.id.in_(payment_ids))
    ).all()
    created_lookup = {row.id: row.created_at for row in payment_rows}

    approvals: list[PaymentApproval] = (
        PaymentApproval.query.filter(PaymentApproval.payment_request_id.in_(payment_ids))
        .order_by(PaymentApproval.payment_request_id.asc(), PaymentApproval.decided_at.asc())
        .all()
    )

    last_seen: dict[int, datetime] = {
        pid: created_lookup.get(pid) for pid in payment_ids
    }
    durations: dict[str, list[float]] = defaultdict(list)

    for approval in approvals:
        stage_key = _stage_key_from_approval(approval)
        if not stage_key:
            last_seen[approval.payment_request_id] = approval.decided_at or last_seen.get(approval.payment_request_id)
            continue

        start = last_seen.get(approval.payment_request_id) or created_lookup.get(approval.payment_request_id)
        end = approval.decided_at
        if not start or not end or end <= start:
            last_seen[approval.payment_request_id] = end or start
            continue

        duration_days = (end - start).total_seconds() / 86400
        durations[stage_key].append(duration_days)
        last_seen[approval.payment_request_id] = end

    metrics = []
    for stage, values in durations.items():
        if not values:
            continue
        avg_days = sum(values) / len(values)
        metrics.append({
            "stage": stage,
            "average_days": round(avg_days, 2),
            "count": len(values),
        })

    metrics.sort(key=lambda item: item["stage"])
    return metrics
