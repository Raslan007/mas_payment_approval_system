# blueprints/main/routes.py

from datetime import datetime, timedelta

from flask import redirect, url_for, render_template, request, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from permissions import role_required
from . import main_bp
from models import PaymentRequest, Project, PaymentApproval

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
@role_required(
    "admin",
    "engineering_manager",
    "chairman",
    "finance",
    "engineer",
    "project_manager",
)
def dashboard():
    role_name = current_user.role.name if current_user.role else ""
    notifications_count = (
        current_user.notifications.filter_by(is_read=False).count()
        if current_user.is_authenticated
        else 0
    )
    messages_count = getattr(current_user, "unread_messages", 0) or 0

    def _add_tile(title, description, icon, endpoint=None, url=None, badge=None, roles=None):
        if roles and role_name not in roles:
            return
        destination = url or (url_for(endpoint) if endpoint else "#")
        tiles.append(
            {
                "title": title,
                "description": description,
                "icon": icon,
                "url": destination,
                "badge": badge,
            }
        )

    tiles: list[dict[str, str | int]] = []

    _add_tile(
        "الإشعارات",
        "رسائل وتنبيهات النظام الموجهة لك",
        "fa-regular fa-bell",
        endpoint="notifications.list_notifications",
        badge=notifications_count,
    )
    _add_tile(
        "الدفعات",
        "إدارة طلبات الدفعات ومراحل الاعتماد",
        "fa-solid fa-wallet",
        endpoint="payments.index",
    )
    _add_tile(
        "جاهزة للصرف",
        "دفعات معتمدة تنتظر التسجيل المالي",
        "fa-solid fa-circle-check",
        endpoint="payments.finance_eng_approved",
        roles={"finance", "admin", "engineering_manager"},
    )
    _add_tile(
        "لوحة الإدارة الهندسية",
        "متابعة الدفعات عبر الإدارة الهندسية",
        "fa-solid fa-sitemap",
        endpoint="main.eng_dashboard",
        roles={"admin", "engineering_manager", "chairman"},
    )
    _add_tile(
        "المشروعات",
        "إدارة قائمة المشروعات والمعلومات الأساسية",
        "fa-solid fa-building",
        endpoint="projects.list_projects",
        roles={"admin", "engineering_manager", "chairman", "dc"},
    )
    _add_tile(
        "الموردون / المقاولون",
        "تتبع بيانات الموردين وتحديثها",
        "fa-solid fa-users",
        endpoint="suppliers.list_suppliers",
        roles={"admin", "engineering_manager", "chairman", "dc"},
    )
    _add_tile(
        "المستخدمون",
        "إدارة صلاحيات وحسابات المستخدمين",
        "fa-solid fa-user-gear",
        endpoint="users.list_users",
        roles={"admin", "dc"},
    )
    _add_tile(
        "تقارير مالية",
        "تصدير بيانات وتقارير سريعة",
        "fa-solid fa-file-export",
        url=url_for("finance.workbench") if role_name in {"admin", "finance", "engineering_manager"} else "#",
        roles={"admin", "finance", "engineering_manager"},
    )

    return render_template(
        "dashboard.html",
        page_title="لوحة التطبيقات",
        tiles=tiles,
        notifications_count=notifications_count,
        messages_count=messages_count,
        role_name=role_name,
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
