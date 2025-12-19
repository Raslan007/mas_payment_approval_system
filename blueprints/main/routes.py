# blueprints/main/routes.py

from datetime import datetime, timedelta

from flask import redirect, url_for, render_template, request, flash
from flask_login import login_required, current_user
from sqlalchemy import func, false, inspect
from sqlalchemy.orm import selectinload

from extensions import db
from permissions import role_required
from . import main_bp
from models import PaymentRequest, Project, PaymentApproval, user_projects

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


def _scoped_dashboard_query():
    """
    Build a base query for dashboard data respecting the current user's role and project access.
    """

    role_name = current_user.role.name if current_user.role else None
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
            .filter(user_projects.c.user_id == current_user.id)
            .all()
        )
        return [row.project_id for row in rows]

    if role_name == "project_manager":
        scoped_project_ids = _project_ids_from_link_table()
        if current_user.project_id:
            scoped_project_ids.append(current_user.project_id)
        scoped_project_ids = list({pid for pid in scoped_project_ids if pid})
        if scoped_project_ids:
            query = query.filter(PaymentRequest.project_id.in_(scoped_project_ids))
        else:
            query = query.filter(false())
    elif role_name == "engineer":
        query = query.filter(PaymentRequest.created_by == current_user.id)
    elif role_name == "dc":
        query = query.filter(false())

    return query, role_name, scoped_project_ids

@main_bp.route("/")
@login_required
def index():
    """
    توجيه المستخدم إلى الصفحة الصحيحة بعد تسجيل الدخول.
    """

    role_name = current_user.role.name if current_user.role else None

    # في حال لم يتم تعيين دور للمستخدم بعد
    if role_name is None:
        flash(
            "حسابك غير مرتبط بدور حتى الآن. يرجى التواصل مع مسؤول النظام أو موظف البيانات لتحديد الصلاحيات.",
            "warning",
        )
        return redirect(url_for("main.no_role"))

    # مدير مشروع → دفعاته
    if role_name == "project_manager":
        return redirect(url_for("payments.index"))

    # مهندس → دفعاته
    if role_name == "engineer":
        return redirect(url_for("payments.index"))

    # Data Entry (DC) → إدارة المستخدمين
    if role_name == "dc":
        return redirect(url_for("users.list_users"))

    if role_name == "payment_notifier":
        return redirect(url_for("payments.finance_eng_approved"))

    # admin + engineering_manager + chairman + finance → لوحة التحكم العامة
    if role_name in ("admin", "engineering_manager", "chairman", "finance"):
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
@role_required("admin", "engineering_manager", "chairman", "finance")
def dashboard():
    """
    لوحة تحكم عامة للدفعات:
    - إجمالي عدد الدفعات
    - إجمالي قيمة الدفعات (طلب المهندس)
    - المبالغ المصروفة فعلياً (amount_finance)
    - المبالغ المعتمدة/منتظرة ولم تُصرف بعد
    - توزيع مبالغ الدفعات حسب الحالة
    - توزيع مبالغ الدفعات حسب المشروع
    """

    base_q, role_name, _ = _scoped_dashboard_query()

    # إعداد التصفح (Pagination) لتحميل عدد محدود من الدفعات في كل صفحة
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

    status_counts_rows = (
        base_q.order_by(None)
        .with_entities(PaymentRequest.status, func.count(PaymentRequest.id))
        .group_by(PaymentRequest.status)
        .all()
    )
    status_counts = {status: count for status, count in status_counts_rows}

    total_count = sum(status_counts.values())

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

    # إجمالي قيمة الدفعات المطلوبة (مبلغ المهندس)
    total_amount = (
        base_q.with_entities(func.coalesce(func.sum(PaymentRequest.amount), 0.0))
        .scalar()
        or 0.0
    )

    # -----------------------------
    # المبالغ المصروفة فعلياً
    # -----------------------------
    paid_q = base_q.filter(PaymentRequest.status == STATUS_PAID)
    # نستخدم amount_finance، ولو فاضي نرجع لـ amount احتياطيًا
    total_paid = (
        paid_q.with_entities(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.amount_finance,
                        PaymentRequest.amount,
                        0.0,
                    )
                ),
                0.0,
            )
        ).scalar()
        or 0.0
    )

    # -----------------------------
    # في انتظار المالية (ما قبل إدخال المبلغ الفعلي)
    # -----------------------------
    waiting_fin_q = base_q.filter(PaymentRequest.status == STATUS_PENDING_FIN)
    total_waiting_finance = (
        waiting_fin_q.with_entities(
            func.coalesce(func.sum(PaymentRequest.amount), 0.0)
        )
        .scalar()
        or 0.0
    )

    # -----------------------------
    # جاهزة للصرف (معتمدة ماليًا ولم تُسجل كـ تم الصرف)
    # هنا نستخدم المبلغ المالي الفعلي amount_finance
    # -----------------------------
    approved_not_paid_q = base_q.filter(
        PaymentRequest.status == STATUS_READY_FOR_PAYMENT
    )
    total_approved_not_paid = (
        approved_not_paid_q.with_entities(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.amount_finance,
                        PaymentRequest.amount,
                        0.0,
                    )
                ),
                0.0,
            )
        ).scalar()
        or 0.0
    )

    # -----------------------------
    # مبالغ الدفعات المصروفة هذا الشهر (مالية فعلية)
    # -----------------------------
    now = datetime.utcnow()
    start_of_month = datetime(now.year, now.month, 1)
    paid_this_month = (
        paid_q.filter(PaymentRequest.updated_at >= start_of_month)
        .with_entities(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.amount_finance,
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

    # بارامترات الفلترة الحالية (مع حذف أرقام الصفحات) للاستخدام في روابط التصفح
    query_params = request.args.to_dict()
    query_params.pop("page", None)
    query_params.pop("per_page", None)

    # -------------------------------------------------
    # توزيع حسب الحالة (نستخدم amount أو amount_finance)
    # -------------------------------------------------
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
                        PaymentRequest.amount_finance,
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

    overdue_threshold = datetime.utcnow() - timedelta(days=7)
    overdue_statuses = pending_review_statuses | {STATUS_READY_FOR_PAYMENT}
    overdue_rows = (
        base_q.filter(
            PaymentRequest.status.in_(overdue_statuses),
            PaymentRequest.updated_at < overdue_threshold,
        )
        .with_entities(PaymentRequest.status, func.count(PaymentRequest.id))
        .group_by(PaymentRequest.status)
        .all()
    )
    overdue_by_stage = {status: count for status, count in overdue_rows}
    overdue_total = sum(overdue_by_stage.values())
    overdue_stage_breakdown = [
        {
            "status": status,
            "label": status_labels.get(status, status),
            "count": overdue_by_stage.get(status, 0),
        }
        for status in (
            STATUS_PENDING_PM,
            STATUS_PENDING_ENG,
            STATUS_PENDING_FIN,
            STATUS_READY_FOR_PAYMENT,
        )
    ]

    # -------------------------------------------------
    # توزيع حسب المشروع (حالياً على أساس المبلغ المطلوب من المهندس)
    # -------------------------------------------------
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
            func.coalesce(PaymentRequest.amount_finance, PaymentRequest.amount, 0.0).label(
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
    }

    return render_template(
        "dashboard.html",
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
        kpis=kpis,
        overdue_stage_breakdown=overdue_stage_breakdown,
        action_required=action_required,
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
