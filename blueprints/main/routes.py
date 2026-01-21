# blueprints/main/routes.py

from datetime import datetime, timedelta
from functools import lru_cache

from flask import redirect, url_for, render_template, request, flash, current_app, g
from flask_login import login_required, current_user
from sqlalchemy import case, func, false, or_, inspect
from sqlalchemy.orm import selectinload

from extensions import db
from permissions import role_required
from . import main_bp
from .dashboard_metrics import build_status_chips
from models import (
    PaymentRequest,
    Project,
    PaymentApproval,
    PurchaseOrder,
    Supplier,
    PaymentFinanceAdjustment,
    SupplierLedgerEntry,
)
from project_scopes import get_scoped_project_ids
from .navigation import get_launcher_modules
from .dashboard_helpers import (
    compute_overdue_items,
    compute_stage_sla_metrics,
    resolve_sla_thresholds,
)
from blueprints.payments.inbox_queries import scoped_inbox_base_query
from blueprints.payments.routes import PURCHASE_ORDER_EXCLUDED_STATUSES
from blueprints.purchase_orders.routes import (
    STATUS_META as PURCHASE_ORDER_STATUS_META,
    ALLOWED_STATUSES as PURCHASE_ORDER_ALLOWED_STATUSES,
)

# تعريف الحالات مثل ملف payments.routes
STATUS_DRAFT = "draft"
STATUS_PENDING_PM = "pending_pm"
STATUS_PENDING_ENG = "pending_eng"
STATUS_PENDING_FIN = "pending_finance"
STATUS_READY_FOR_PAYMENT = "ready_for_payment"
STATUS_PAID = "paid"
STATUS_REJECTED = "rejected"
ALLOWED_STATUSES = {
    STATUS_DRAFT,
    STATUS_PENDING_PM,
    STATUS_PENDING_ENG,
    STATUS_PENDING_FIN,
    STATUS_READY_FOR_PAYMENT,
    STATUS_PAID,
    STATUS_REJECTED,
}
STATUS_GROUPS: dict[str, set[str]] = {
    "outstanding": {
        STATUS_PENDING_PM,
        STATUS_PENDING_ENG,
        STATUS_PENDING_FIN,
        STATUS_READY_FOR_PAYMENT,
    },
    "paid": {
        STATUS_PAID,
    },
}


@lru_cache(maxsize=1)
def _purchase_orders_column_names() -> set[str]:
    inspector = inspect(db.engine)
    if not inspector.has_table("purchase_orders"):
        return set()
    return {column["name"] for column in inspector.get_columns("purchase_orders")}


def _purchase_orders_has_deleted_at() -> bool:
    return "deleted_at" in _purchase_orders_column_names()


def _scoped_dashboard_query():
    """
    Build a base query for dashboard data respecting the current user's role and project access.
    """

    query, role_name, scoped_project_ids = scoped_inbox_base_query(current_user)
    return query, role_name, scoped_project_ids


def _resolve_launcher_modules():
    return get_launcher_modules(current_user)


@main_bp.app_context_processor
def inject_launcher_modules():
    return {"launcher_modules": _resolve_launcher_modules()}


@main_bp.route("/")
@login_required
def index():
    """
    توجيه المستخدم إلى الصفحة الصحيحة بعد تسجيل الدخول.
    """

    role_name = current_user.role.name if current_user.role else None
    normalized_role = "engineer" if role_name == "project_engineer" else role_name

    # في حال لم يتم تعيين دور للمستخدم بعد
    if role_name is None:
        flash(
            "حسابك غير مرتبط بدور حتى الآن. يرجى التواصل مع مسؤول النظام أو موظف البيانات لتحديد الصلاحيات.",
            "warning",
        )
        return redirect(url_for("main.no_role"))

    # مدير مشروع → دفعاته
    if normalized_role == "project_manager":
        return redirect(url_for("main.dashboard"))

    # مهندس → دفعاته
    if normalized_role == "engineer":
        return redirect(url_for("main.dashboard"))

    # Data Entry (DC) → إدارة المستخدمين
    if normalized_role == "dc":
        return redirect(url_for("main.dashboard"))

    if normalized_role == "payment_notifier":
        return redirect(url_for("main.dashboard"))

    if normalized_role == "procurement":
        return redirect(url_for("main.dashboard"))

    # admin + engineering_manager + chairman + finance → لوحة التحكم العامة
    if normalized_role in ("admin", "engineering_manager", "chairman", "finance"):
        return redirect(url_for("main.dashboard"))

    # fallback لأدوار غير معروفة
    flash(
        "تعذر تحديد وجهة الحساب. يرجى التواصل مع مسؤول النظام لتحديد الصلاحيات المناسبة.",
        "warning",
    )
    return redirect(url_for("main.no_role"))


@main_bp.route("/no-role")
@login_required
def no_role():
    """صفحة توضيحية للمستخدمين الذين لم يتم تعيين دور لهم بعد."""

    return render_template(
        "main/no_role.html",
        page_title="بانتظار تحديد الصلاحيات",
    )


# -------------------------------------------------------------------
# لوحة التحكم العامة للدفعات
# -------------------------------------------------------------------
@main_bp.route("/dashboard")
@role_required(
    "admin",
    "engineering_manager",
    "chairman",
    "finance",
    "engineer",
    "project_manager",
    "payment_notifier",
    "dc",
    "planning",
    "procurement",
)
def dashboard():
    base_q, role_name, _ = _scoped_dashboard_query()
    notifications_count = (
        current_user.notifications.filter_by(is_read=False).count()
        if current_user.is_authenticated
        else 0
    )
    messages_count = getattr(current_user, "unread_messages", 0) or 0
    tiles = _resolve_launcher_modules()
    # Enrich notifications badge for tiles when available
    for tile in tiles:
        if tile.get("key") == "notifications":
            tile["badge"] = notifications_count

    status_chips = build_status_chips(
        base_q,
        role_name,
        notifications_count=notifications_count,
        include_notifications=hasattr(current_user, "notifications"),
        user_id=getattr(current_user, "id", None),
    )

    return render_template(
        "dashboard.html",
        page_title="لوحة التطبيقات",
        tiles=tiles,
        notifications_count=notifications_count,
        messages_count=messages_count,
        role_name=role_name,
        status_chips=status_chips,
    )


@main_bp.route("/overview")
@role_required("admin", "engineering_manager", "chairman", "finance", "procurement", "accounts")
def overview():
    """
    نظرة إجمالية على مبالغ وعدد الدفعات حسب الحالة والمشروعات.
    """

    base_q, role_name, _ = _scoped_dashboard_query()

    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get("per_page", 20))
    except (TypeError, ValueError):
        per_page = 20

    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)

    finance_amount_or_amount = func.coalesce(
        PaymentRequest.finance_amount,
        PaymentRequest.amount,
        0.0,
    )
    aggregates = (
        base_q.order_by(None)
        .with_entities(
            func.count(PaymentRequest.id).label("total_count"),
            func.sum(case((PaymentRequest.status == STATUS_DRAFT, 1), else_=0)).label("draft_count"),
            func.sum(case((PaymentRequest.status == STATUS_PENDING_PM, 1), else_=0)).label("pending_pm_count"),
            func.sum(case((PaymentRequest.status == STATUS_PENDING_ENG, 1), else_=0)).label("pending_eng_count"),
            func.sum(case((PaymentRequest.status == STATUS_PENDING_FIN, 1), else_=0)).label("pending_fin_count"),
            func.sum(case((PaymentRequest.status == STATUS_READY_FOR_PAYMENT, 1), else_=0)).label(
                "ready_for_payment_count"
            ),
            func.sum(case((PaymentRequest.status == STATUS_PAID, 1), else_=0)).label("paid_count"),
            func.sum(case((PaymentRequest.status == STATUS_REJECTED, 1), else_=0)).label("rejected_count"),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0).label("total_amount"),
            func.coalesce(
                func.sum(case((PaymentRequest.status == STATUS_PAID, finance_amount_or_amount), else_=0.0)), 0.0
            ).label("total_paid"),
            func.coalesce(
                func.sum(case((PaymentRequest.status == STATUS_PENDING_FIN, PaymentRequest.amount), else_=0.0)), 0.0
            ).label("total_waiting_finance"),
            func.coalesce(
                func.sum(
                    case(
                        (PaymentRequest.status == STATUS_READY_FOR_PAYMENT, finance_amount_or_amount),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("total_approved_not_paid"),
        )
        .first()
    )
    if aggregates is None:
        aggregates = type(
            "Aggregates",
            (),
            {
                "total_count": 0,
                "draft_count": 0,
                "pending_pm_count": 0,
                "pending_eng_count": 0,
                "pending_fin_count": 0,
                "ready_for_payment_count": 0,
                "paid_count": 0,
                "rejected_count": 0,
                "total_amount": 0.0,
                "total_paid": 0.0,
                "total_waiting_finance": 0.0,
                "total_approved_not_paid": 0.0,
            },
        )()

    status_counts = {
        STATUS_DRAFT: aggregates.draft_count or 0,
        STATUS_PENDING_PM: aggregates.pending_pm_count or 0,
        STATUS_PENDING_ENG: aggregates.pending_eng_count or 0,
        STATUS_PENDING_FIN: aggregates.pending_fin_count or 0,
        STATUS_READY_FOR_PAYMENT: aggregates.ready_for_payment_count or 0,
        STATUS_PAID: aggregates.paid_count or 0,
        STATUS_REJECTED: aggregates.rejected_count or 0,
    }

    total_count = aggregates.total_count or 0

    pending_review_statuses = {STATUS_PENDING_PM, STATUS_PENDING_ENG, STATUS_PENDING_FIN}
    pending_review_count = sum(status_counts.get(status, 0) for status in pending_review_statuses)
    approved_count = status_counts.get(STATUS_READY_FOR_PAYMENT, 0)
    paid_count = status_counts.get(STATUS_PAID, 0)
    rejected_count = status_counts.get(STATUS_REJECTED, 0)

    ordered_q = base_q.options(selectinload(PaymentRequest.project)).order_by(
        PaymentRequest.created_at.desc(), PaymentRequest.id.desc()
    )

    pagination = ordered_q.paginate(
        page=page, per_page=per_page, error_out=False, count=False
    )
    pagination.total = total_count
    payments_page = pagination.items

    total_amount = aggregates.total_amount or 0.0
    total_paid = aggregates.total_paid or 0.0
    total_waiting_finance = aggregates.total_waiting_finance or 0.0
    total_approved_not_paid = aggregates.total_approved_not_paid or 0.0

    now = datetime.utcnow()
    start_of_month = datetime(now.year, now.month, 1)
    paid_this_month = (
        base_q.filter(
            PaymentRequest.status == STATUS_PAID,
            PaymentRequest.updated_at >= start_of_month,
        )
        .with_entities(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.finance_amount,
                        PaymentRequest.amount,
                        0.0,
                    )
                ),
                0.0,
            )
        )
        .scalar()
        or 0.0
    )

    query_params = request.args.to_dict()
    query_params.pop("page", None)
    query_params.pop("per_page", None)

    status_labels = {
        STATUS_DRAFT: "مسودة (مدخل بواسطة المهندس)",
        STATUS_PENDING_PM: "تحت مراجعة مدير المشروع",
        STATUS_PENDING_ENG: "تحت مراجعة الإدارة الهندسية",
        STATUS_PENDING_FIN: "في انتظار اعتماد المالية",
        STATUS_READY_FOR_PAYMENT: "جاهزة للصرف",
        STATUS_PAID: "تم الصرف",
        STATUS_REJECTED: "مرفوضة",
    }

    totals_by_status = []
    status_amount_rows = (
        base_q.with_entities(
            PaymentRequest.status.label("status"),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0).label("total_amount"),
            func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.finance_amount,
                        PaymentRequest.amount,
                        0.0,
                    )
                ),
                0.0,
            ).label("total_finance_amount"),
        )
        .group_by(PaymentRequest.status)
        .all()
    )
    amount_lookup = {row.status: row for row in status_amount_rows}

    totals_by_status = []
    for status, label in status_labels.items():
        row = amount_lookup.get(status)
        if status in (STATUS_READY_FOR_PAYMENT, STATUS_PAID):
            amount = row.total_finance_amount if row else 0.0
        else:
            amount = row.total_amount if row else 0.0

        totals_by_status.append(
            {
                "status": status,
                "label": label,
                "total_amount": amount,
            }
        )

    ready_for_payment_amount = (
        amount_lookup.get(STATUS_READY_FOR_PAYMENT).total_finance_amount
        if amount_lookup.get(STATUS_READY_FOR_PAYMENT)
        else 0.0
    )

    pending_amount = sum(
        (amount_lookup.get(status).total_amount if amount_lookup.get(status) else 0.0)
        for status in pending_review_statuses
    )
    total_outstanding_amount = pending_amount + ready_for_payment_amount

    legacy_view_roles = {
        "admin",
        "engineering_manager",
        "procurement",
        "accounts",
        "chairman",
        "finance",
    }
    show_legacy_liabilities = role_name in legacy_view_roles
    legacy_liabilities_total = None
    if show_legacy_liabilities:
        legacy_liabilities_total = (
            db.session.query(
                func.coalesce(
                    func.sum(
                        case(
                            (SupplierLedgerEntry.direction == "debit", SupplierLedgerEntry.amount),
                            (SupplierLedgerEntry.direction == "credit", -SupplierLedgerEntry.amount),
                            else_=0,
                        )
                    ),
                    0.0,
                )
            )
            .filter(SupplierLedgerEntry.voided_at.is_(None))
            .scalar()
            or 0.0
        )

    sla_thresholds = resolve_sla_thresholds(current_app.config)
    sla_statuses = {stage for stage in sla_thresholds.keys()}
    overdue_candidates = (
        base_q.options(selectinload(PaymentRequest.project), selectinload(PaymentRequest.supplier))
        .filter(PaymentRequest.status.in_(sla_statuses))
        .order_by(PaymentRequest.updated_at.asc(), PaymentRequest.id.asc())
        .limit(300)
        .all()
    )
    overdue_data = compute_overdue_items(overdue_candidates, sla_thresholds)
    overdue_total = overdue_data["summary"]["total"]
    overdue_stage_breakdown = [
        {
            "status": stage,
            "label": status_labels.get(stage, stage),
            "count": overdue_data["summary"]["breakdown"].get(stage, 0),
        }
        for stage in (STATUS_PENDING_PM, STATUS_PENDING_ENG, STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT)
    ]
    overdue_stage_with_highest_delay = overdue_data["summary"]["worst_stage"]
    aging_kpis = {
        "oldest_overdue_days": overdue_data["summary"]["oldest_days"],
        "worst_stage_label": status_labels.get(overdue_stage_with_highest_delay, overdue_stage_with_highest_delay)
        if overdue_stage_with_highest_delay
        else None,
    }
    top_overdue = overdue_data["items"][:10]

    totals_by_project = (
        base_q.join(Project, PaymentRequest.project_id == Project.id)
        .with_entities(
            Project.project_name.label("project_name"),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0).label("total_amount"),
        )
        .group_by(Project.id, Project.project_name)
        .order_by(Project.project_name.asc())
        .all()
    )

    actionable_statuses = {
        "admin": {STATUS_PENDING_PM, STATUS_PENDING_ENG, STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT},
        "engineering_manager": {STATUS_PENDING_PM, STATUS_PENDING_ENG},
        "finance": {STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT},
        "chairman": set(),
    }
    action_required_statuses = actionable_statuses.get(role_name or "", set())
    action_required = []
    action_required_total = 0
    if action_required_statuses:
        action_required_base = base_q.filter(PaymentRequest.status.in_(action_required_statuses))
        action_required_total = action_required_base.order_by(None).count()
        action_required = (
            action_required_base.options(selectinload(PaymentRequest.project))
            .order_by(PaymentRequest.updated_at.desc(), PaymentRequest.id.desc())
            .limit(10)
            .all()
        )

    ready_for_payment_list = []
    if role_name in ("finance", "admin"):
        ready_for_payment_list = (
            base_q.options(
                selectinload(PaymentRequest.project),
                selectinload(PaymentRequest.supplier),
            )
            .filter(PaymentRequest.status == STATUS_READY_FOR_PAYMENT)
            .order_by(PaymentRequest.updated_at.asc(), PaymentRequest.id.asc())
            .limit(10)
            .all()
        )

    thirty_days_ago = datetime.utcnow().date() - timedelta(days=29)
    date_cutoff = datetime.combine(thirty_days_ago, datetime.min.time())
    daily_rows = (
        base_q.filter(PaymentRequest.created_at >= date_cutoff)
        .with_entities(
            func.date(PaymentRequest.created_at).label("day"),
            func.count(PaymentRequest.id).label("count"),
        )
        .group_by(func.date(PaymentRequest.created_at))
        .order_by(func.date(PaymentRequest.created_at))
        .all()
    )
    daily_lookup = {
        (row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)): row.count
        for row in daily_rows
    }
    daily_labels = []
    daily_values = []
    for offset in range(30):
        day = thirty_days_ago + timedelta(days=offset)
        label = day.isoformat()
        daily_labels.append(label)
        daily_values.append(daily_lookup.get(label, 0))

    status_chart_labels = [label for _, label in status_labels.items()]
    status_chart_values = [status_counts.get(status, 0) for status in status_labels.keys()]

    def _amount_for_status(status: str) -> float:
        row = amount_lookup.get(status)
        if not row:
            return 0.0
        if status in (STATUS_READY_FOR_PAYMENT, STATUS_PAID):
            return row.total_finance_amount
        return row.total_amount

    workflow_stages = [
        (STATUS_DRAFT, "مراجعة المهندس"),
        (STATUS_PENDING_PM, "مراجعة مدير المشروع"),
        (STATUS_PENDING_ENG, "مراجعة الإدارة الهندسية"),
        (STATUS_PENDING_FIN, "مراجعة المالية"),
        (STATUS_READY_FOR_PAYMENT, "معتمد للصرف"),
        (STATUS_PAID, "تم الصرف"),
        (STATUS_REJECTED, "مرفوض"),
    ]
    workflow_funnel = [
        {
            "status": status,
            "label": label,
            "count": status_counts.get(status, 0),
            "amount": _amount_for_status(status),
        }
        for status, label in workflow_stages
    ]

    trend_start_date = datetime.utcnow().date() - timedelta(days=59)
    trend_start = datetime.combine(trend_start_date, datetime.min.time())
    status_for_trend = STATUS_GROUPS["outstanding"] | {STATUS_PAID}
    trend_rows = (
        base_q.filter(
            PaymentRequest.status.in_(status_for_trend),
            func.coalesce(PaymentRequest.updated_at, PaymentRequest.created_at) >= trend_start,
        )
        .with_entities(
            func.date(func.coalesce(PaymentRequest.updated_at, PaymentRequest.created_at)).label("day"),
            PaymentRequest.status,
            func.coalesce(PaymentRequest.finance_amount, PaymentRequest.amount, 0.0).label(
                "finance_amount"
            ),
            func.coalesce(PaymentRequest.amount, 0.0).label("requested_amount"),
        )
        .all()
    )
    trend_lookup: dict[str, dict[str, float]] = {}
    outstanding_statuses = STATUS_GROUPS["outstanding"]
    for row in trend_rows:
        day_str = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
        bucket = trend_lookup.setdefault(day_str, {"outstanding": 0.0, "paid": 0.0})
        amount_for_status = row.finance_amount if row.status in (STATUS_READY_FOR_PAYMENT, STATUS_PAID) else row.requested_amount
        if row.status in outstanding_statuses:
            bucket["outstanding"] += amount_for_status
        if row.status == STATUS_PAID:
            bucket["paid"] += amount_for_status

    cash_flow_labels = []
    outstanding_values = []
    paid_values = []
    for offset in range(60):
        day = trend_start_date + timedelta(days=offset)
        label = day.isoformat()
        cash_flow_labels.append(label)
        values = trend_lookup.get(label, {"outstanding": 0.0, "paid": 0.0})
        outstanding_values.append(values["outstanding"])
        paid_values.append(values["paid"])
    cash_flow_chart = {
        "labels": cash_flow_labels,
        "datasets": {
            "outstanding": outstanding_values,
            "paid": paid_values,
        },
    }

    payment_ids_for_sla = [row.id for row in base_q.with_entities(PaymentRequest.id).limit(500).all()]
    sla_metrics = compute_stage_sla_metrics(payment_ids_for_sla)

    dashboard_alerts = []
    if overdue_total:
        dashboard_alerts.append(
            {
                "level": "danger",
                "text": f"هناك {overdue_total} دفعات متأخرة زمنياً ضمن نطاق صلاحياتك.",
            }
        )
    if aging_kpis["worst_stage_label"]:
        dashboard_alerts.append(
            {
                "level": "warning",
                "text": f"أكثر مرحلة بها تأخير: {aging_kpis['worst_stage_label']}.",
            }
        )
    if action_required_total:
        dashboard_alerts.append(
            {
                "level": "info",
                "text": f"يوجد {action_required_total} دفعات تحتاج إلى إجراء منك.",
            }
        )

    listing_base_url = url_for("payments.index")
    status_filters = {"outstanding_group": "outstanding", "paid": STATUS_PAID}

    kpis = {
        "total": total_count,
        "pending_review": pending_review_count,
        "overdue_total": overdue_total,
        "action_required": action_required_total,
        "outstanding_amount": total_outstanding_amount,
        "paid_this_month": paid_this_month,
        "ready_amount": ready_for_payment_amount,
        "approved": approved_count,
        "paid": paid_count,
        "rejected": rejected_count,
        "total_amount": total_amount,
        "oldest_overdue": aging_kpis["oldest_overdue_days"],
        "legacy_liabilities": legacy_liabilities_total if legacy_liabilities_total is not None else None,
    }

    return render_template(
        "overview.html",
        page_title="لوحة التحكم العامة للدفعات",
        payments=payments_page,
        pagination=pagination,
        page=page,
        per_page=per_page,
        query_params=query_params,
        total_count=total_count,
        total_amount=total_amount,
        total_paid=total_paid,
        total_waiting_finance=total_waiting_finance,
        total_approved_not_paid=total_approved_not_paid,
        ready_for_payment_list=ready_for_payment_list,
        totals_by_status=totals_by_status,
        totals_by_project=totals_by_project,
        status_labels=status_labels,
        kpis=kpis,
        overdue_stage_breakdown=overdue_stage_breakdown,
        aging_kpis=aging_kpis,
        top_overdue=top_overdue,
        sla_metrics=sla_metrics,
        dashboard_alerts=dashboard_alerts,
        action_required=action_required,
        show_legacy_liabilities=show_legacy_liabilities,
        daily_chart={"labels": daily_labels, "values": daily_values},
        status_chart={"labels": status_chart_labels, "values": status_chart_values},
        workflow_funnel=workflow_funnel,
        cash_flow_chart=cash_flow_chart,
        listing_base_url=listing_base_url,
        status_filters=status_filters,
    )


# -------------------------------------------------------------------
# لوحة الإدارة الهندسية (كما اتفقنا سابقًا)
# -------------------------------------------------------------------
@main_bp.route("/eng-dashboard")
@role_required("admin", "engineering_manager", "chairman")
def eng_dashboard():
    """
    لوحة الإدارة الهندسية:
    - متاحة فقط لـ admin + مدير الإدارة الهندسية + رئيس مجلس الإدارة.
    - تعتمد على حالات الدفعات بعد مرورها على الإدارة الهندسية.
    """

    # قراءة الفلاتر من الـ Query String (لو موجودة) مع تنقية القيم
    filters = {"date_from": "", "date_to": "", "project_id": "", "status": ""}

    try:
        project_id = int(request.args.get("project_id", ""))
        if project_id < 1:
            project_id = None
        else:
            filters["project_id"] = str(project_id)
    except (TypeError, ValueError):
        project_id = None

    def _safe_date_arg(param: str) -> datetime | None:
        raw = request.args.get(param)
        if not raw:
            return None
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
            filters[param] = parsed.strftime("%Y-%m-%d")
            return parsed
        except (TypeError, ValueError):
            return None

    date_from_dt = _safe_date_arg("date_from")
    date_to_input = _safe_date_arg("date_to")
    date_to_dt = date_to_input + timedelta(days=1) if date_to_input else None

    status_filter = (request.args.get("status") or "").strip()
    if status_filter in ALLOWED_STATUSES:
        filters["status"] = status_filter

    # قائمة المشاريع
    projects = Project.query.order_by(Project.project_name.asc()).all()

    # كويري أساسي يُطبق عليه فلاتر المشروع والتاريخ
    base_q = PaymentRequest.query

    if project_id:
        base_q = base_q.filter(PaymentRequest.project_id == project_id)
    if status_filter in ALLOWED_STATUSES:
        base_q = base_q.filter(PaymentRequest.status == status_filter)

    if date_from_dt:
        base_q = base_q.filter(PaymentRequest.created_at >= date_from_dt)

    if date_to_dt:
        base_q = base_q.filter(PaymentRequest.created_at < date_to_dt)

    # ---- الإحصائيات الرئيسية للكروت العليا ----

    # دفعات تحت مراجعة الإدارة الهندسية
    pending_eng_q = base_q.filter(PaymentRequest.status == STATUS_PENDING_ENG)
    pending_eng_count = pending_eng_q.count()
    pending_eng_total = (
        pending_eng_q.with_entities(func.coalesce(func.sum(PaymentRequest.amount), 0.0))
        .scalar()
        or 0.0
    )

    # دفعات في انتظار المالية
    waiting_finance_q = base_q.filter(PaymentRequest.status == STATUS_PENDING_FIN)
    waiting_finance_count = waiting_finance_q.count()
    waiting_finance_total = (
        waiting_finance_q.with_entities(
            func.coalesce(func.sum(PaymentRequest.amount), 0.0)
        )
        .scalar()
        or 0.0
    )

    # مبالغ معتمدة من الإدارة الهندسية (تم تمريرها للمالية أو بعد ذلك)
    approved_after_eng_q = base_q.filter(
        PaymentRequest.status.in_(
            [STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT, STATUS_PAID]
        )
    )
    approved_after_eng_total = (
        approved_after_eng_q.with_entities(
            func.coalesce(func.sum(PaymentRequest.amount), 0.0)
        )
        .scalar()
        or 0.0
    )

    # دفعات مرفوضة (ككل)
    rejected_q = base_q.filter(PaymentRequest.status == STATUS_REJECTED)
    rejected_by_eng_count = rejected_q.count()
    rejected_by_eng_total = (
        rejected_q.with_entities(func.coalesce(func.sum(PaymentRequest.amount), 0.0))
        .scalar()
        or 0.0
    )

    # ---- توزيع حسب المشروع ----

    pending_by_project = (
        pending_eng_q.join(Project, PaymentRequest.project_id == Project.id)
        .with_entities(
            Project.project_name.label("project_name"),
            func.count(PaymentRequest.id).label("count"),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0).label("total_amount"),
        )
        .group_by(Project.id, Project.project_name)
        .order_by(Project.project_name.asc())
        .all()
    )

    waiting_by_project = (
        waiting_finance_q.join(Project, PaymentRequest.project_id == Project.id)
        .with_entities(
            Project.project_name.label("project_name"),
            func.count(PaymentRequest.id).label("count"),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0).label("total_amount"),
        )
        .group_by(Project.id, Project.project_name)
        .order_by(Project.project_name.asc())
        .all()
    )

    # ---- أحدث حركات الاعتماد من الإدارة الهندسية ----
    # نفترض أن step = 'eng_manager' في جدول PaymentApproval لقرارات الإدارة الهندسية
    recent_eng_logs = (
        PaymentApproval.query.filter(PaymentApproval.step == "eng_manager")
        .order_by(PaymentApproval.decided_at.desc())
        .limit(10)
        .all()
    )

    # إحصائيات إضافية عامة (إن احتجتها لاحقاً)
    stats = {
        "total": base_q.count(),
        "draft": base_q.filter(PaymentRequest.status == STATUS_DRAFT).count(),
        "pending_pm": base_q.filter(
            PaymentRequest.status == STATUS_PENDING_PM
        ).count(),
        "pending_eng": pending_eng_count,
        "pending_finance": waiting_finance_count,
        "ready_for_payment": base_q.filter(
            PaymentRequest.status == STATUS_READY_FOR_PAYMENT
        ).count(),
        "paid": base_q.filter(PaymentRequest.status == STATUS_PAID).count(),
        "rejected": rejected_by_eng_count,
    }

    return render_template(
        "eng_dashboard.html",
        page_title="لوحة الإدارة الهندسية",
        filters=filters,
        projects=projects,
        stats=stats,
        pending_eng_count=pending_eng_count,
        pending_eng_total=pending_eng_total,
        waiting_finance_count=waiting_finance_count,
        waiting_finance_total=waiting_finance_total,
        approved_after_eng_total=approved_after_eng_total,
        rejected_by_eng_count=rejected_by_eng_count,
        rejected_by_eng_total=rejected_by_eng_total,
        pending_by_project=pending_by_project,
        waiting_by_project=waiting_by_project,
        recent_eng_logs=recent_eng_logs,
    )


@main_bp.route("/eng-dashboard/commitments")
@role_required("admin", "engineering_manager", "chairman")
def eng_commitments():
    normalized_role = current_user.role.name if current_user.role else None
    if normalized_role == "project_engineer":
        normalized_role = "engineer"

    scoped_ids = get_scoped_project_ids(current_user, role_name=normalized_role)

    filters = {
        "project_id": request.args.get("project_id", type=int),
        "status": (request.args.get("status") or "").strip(),
        "bo_number": (request.args.get("bo_number") or "").strip(),
        "supplier_name": (request.args.get("supplier_name") or "").strip(),
        "sort": (request.args.get("sort") or "due_date").strip(),
        "direction": (request.args.get("direction") or "asc").strip(),
    }

    if filters["status"] not in PURCHASE_ORDER_ALLOWED_STATUSES:
        filters["status"] = ""

    if filters["direction"] not in {"asc", "desc"}:
        filters["direction"] = "asc"

    adjustments_subq = (
        db.session.query(
            PaymentFinanceAdjustment.payment_id.label("payment_id"),
            func.coalesce(func.sum(PaymentFinanceAdjustment.delta_amount), 0).label(
                "adjustments_total"
            ),
        )
        .filter(PaymentFinanceAdjustment.is_void.is_(False))
        .group_by(PaymentFinanceAdjustment.payment_id)
        .subquery()
    )

    effective_amount_expr = (
        func.coalesce(PaymentRequest.finance_amount, 0)
        + func.coalesce(adjustments_subq.c.adjustments_total, 0)
    )

    paid_subq = (
        db.session.query(
            PaymentRequest.purchase_order_id.label("purchase_order_id"),
            func.coalesce(func.sum(effective_amount_expr), 0).label("paid_amount"),
        )
        .outerjoin(
            adjustments_subq,
            PaymentRequest.id == adjustments_subq.c.payment_id,
        )
        .filter(
            PaymentRequest.purchase_order_id.isnot(None),
            PaymentRequest.status == STATUS_PAID,
            PaymentRequest.purchase_order_finalized_at.isnot(None),
        )
        .group_by(PaymentRequest.purchase_order_id)
        .subquery()
    )

    paid_amount_expr = func.coalesce(paid_subq.c.paid_amount, 0)

    base_filters = [
        PurchaseOrder.status.notin_(PURCHASE_ORDER_EXCLUDED_STATUSES),
    ]
    if _purchase_orders_has_deleted_at():
        base_filters.append(PurchaseOrder.deleted_at.is_(None))

    if scoped_ids:
        base_filters.append(PurchaseOrder.project_id.in_(scoped_ids))
    elif normalized_role in {"project_manager", "engineer", "procurement"}:
        base_filters.append(false())

    if filters["project_id"]:
        base_filters.append(PurchaseOrder.project_id == filters["project_id"])
    if filters["status"]:
        base_filters.append(PurchaseOrder.status == filters["status"])
    if filters["bo_number"]:
        base_filters.append(PurchaseOrder.bo_number.ilike(f"%{filters['bo_number']}%"))
    if filters["supplier_name"]:
        supplier_filter = or_(
            Supplier.name.ilike(f"%{filters['supplier_name']}%"),
            PurchaseOrder.supplier_name.ilike(f"%{filters['supplier_name']}%"),
        )
        base_filters.append(supplier_filter)

    sort_map = {
        "bo_number": PurchaseOrder.bo_number,
        "project": Project.project_name,
        "supplier": func.coalesce(Supplier.name, PurchaseOrder.supplier_name),
        "status": PurchaseOrder.status,
        "due_date": PurchaseOrder.due_date,
        "total_amount": PurchaseOrder.total_amount,
        "advance_amount": PurchaseOrder.advance_amount,
        "reserved_amount": PurchaseOrder.reserved_amount,
        "paid_amount": paid_amount_expr,
        "remaining_amount": PurchaseOrder.remaining_amount,
    }
    sort_key = filters["sort"] if filters["sort"] in sort_map else "due_date"
    sort_expr = (
        sort_map[sort_key].desc()
        if filters["direction"] == "desc"
        else sort_map[sort_key].asc()
    )
    if sort_key == "due_date":
        nulls_last_flag = case((PurchaseOrder.due_date.is_(None), 1), else_=0)
        sort_expr = (
            nulls_last_flag.asc(),
            PurchaseOrder.due_date.desc()
            if filters["direction"] == "desc"
            else PurchaseOrder.due_date.asc(),
        )

    commitments_query = (
        PurchaseOrder.query.outerjoin(Project)
        .outerjoin(Supplier)
        .outerjoin(paid_subq, PurchaseOrder.id == paid_subq.c.purchase_order_id)
        .options(selectinload(PurchaseOrder.project), selectinload(PurchaseOrder.supplier))
        .filter(*base_filters)
        .add_columns(paid_amount_expr.label("paid_amount"))
    )

    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    try:
        per_page = int(request.args.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50

    page = max(page, 1)
    per_page = min(max(per_page, 1), 200)

    if isinstance(sort_expr, tuple):
        ordered_query = commitments_query.order_by(*sort_expr, PurchaseOrder.id.desc())
    else:
        ordered_query = commitments_query.order_by(sort_expr, PurchaseOrder.id.desc())

    pagination = ordered_query.paginate(page=page, per_page=per_page, error_out=False)
    purchase_orders = pagination.items

    totals_query = (
        db.session.query(
            func.count(PurchaseOrder.id),
            func.coalesce(func.sum(PurchaseOrder.total_amount), 0),
            func.coalesce(func.sum(PurchaseOrder.reserved_amount), 0),
            func.coalesce(func.sum(paid_amount_expr), 0),
            func.coalesce(func.sum(PurchaseOrder.remaining_amount), 0),
        )
        .select_from(PurchaseOrder)
        .outerjoin(Project)
        .outerjoin(Supplier)
        .outerjoin(paid_subq, PurchaseOrder.id == paid_subq.c.purchase_order_id)
        .filter(*base_filters)
        .first()
    )

    total_count = totals_query[0] if totals_query else 0
    total_commitments = totals_query[1] if totals_query else 0
    total_reserved = totals_query[2] if totals_query else 0
    total_paid = totals_query[3] if totals_query else 0
    total_remaining = totals_query[4] if totals_query else 0

    projects_query = Project.query.order_by(Project.project_name.asc())
    if scoped_ids:
        projects_query = projects_query.filter(Project.id.in_(scoped_ids))
    elif normalized_role in {"project_manager", "engineer", "procurement"}:
        projects_query = projects_query.filter(false())
    projects = projects_query.all()

    query_params = {
        key: value
        for key, value in request.args.items()
        if value and key not in {"sort", "direction", "page"}
    }
    pagination_params = {
        key: value
        for key, value in request.args.items()
        if value and key not in {"page", "per_page"}
    }

    return render_template(
        "eng_commitments.html",
        purchase_orders=purchase_orders,
        pagination=pagination,
        page=page,
        per_page=per_page,
        filters=filters,
        projects=projects,
        status_meta=PURCHASE_ORDER_STATUS_META,
        total_count=total_count,
        total_commitments=total_commitments,
        total_reserved=total_reserved,
        total_paid=total_paid,
        total_remaining=total_remaining,
        query_params=query_params,
        pagination_params=pagination_params,
    )
