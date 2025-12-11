# blueprints/main/routes.py

from datetime import datetime, timedelta

from flask import redirect, url_for, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

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


@main_bp.route("/")
@login_required
def index():
    """
    توجيه المستخدم إلى الصفحة الصحيحة بعد تسجيل الدخول.
    """

    role_name = current_user.role.name if current_user.role else None

    # مدير مشروع → دفعاته
    if role_name == "project_manager":
        return redirect(url_for("payments.index"))

    # مهندس → دفعاته
    if role_name == "engineer":
        return redirect(url_for("payments.index"))

    # Data Entry (DC) → إدارة المستخدمين
    if role_name == "dc":
        return redirect(url_for("users.list_users"))

    # admin + engineering_manager + chairman + finance → لوحة التحكم العامة
    if role_name in ("admin", "engineering_manager", "chairman", "finance"):
        return redirect(url_for("main.dashboard"))

    # fallback
    return redirect(url_for("payments.index"))


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

    base_q = PaymentRequest.query

    # إجمالي عدد الدفعات (أي حالة)
    total_count = base_q.count()

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
    for status, label in status_labels.items():
        s_q = base_q.filter(PaymentRequest.status == status)

        # للحالات بعد المالية نستخدم المبلغ المالي الفعلي
        if status in (STATUS_READY_FOR_PAYMENT, STATUS_PAID):
            sum_expr = func.coalesce(
                func.sum(
                    func.coalesce(
                        PaymentRequest.amount_finance,
                        PaymentRequest.amount,
                        0.0,
                    )
                ),
                0.0,
            )
        else:
            sum_expr = func.coalesce(func.sum(PaymentRequest.amount), 0.0)

        amount = s_q.with_entities(sum_expr).scalar() or 0.0

        totals_by_status.append(
            {
                "status": status,
                "label": label,
                "total_amount": amount,
            }
        )

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

    return render_template(
        "dashboard.html",
        page_title="لوحة التحكم العامة للدفعات",
        total_count=total_count,
        total_amount=total_amount,
        total_paid=total_paid,
        total_waiting_finance=total_waiting_finance,
        total_approved_not_paid=total_approved_not_paid,
        totals_by_status=totals_by_status,
        totals_by_project=totals_by_project,
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

    # قراءة الفلاتر من الـ Query String (لو موجودة)
    filters = {
        "date_from": request.args.get("date_from") or "",
        "date_to": request.args.get("date_to") or "",
        "project_id": request.args.get("project_id") or "",
        "status": request.args.get("status") or "",
    }

    # تحويل التواريخ إلى datetime (بداية اليوم ونهايته)
    date_from_dt = None
    date_to_dt = None

    if filters["date_from"]:
        try:
            date_from_dt = datetime.strptime(filters["date_from"], "%Y-%m-%d")
        except ValueError:
            date_from_dt = None

    if filters["date_to"]:
        try:
            # نضيف يوم كامل حتى يشمل اليوم بالكامل
            date_to_dt = datetime.strptime(filters["date_to"], "%Y-%m-%d") + timedelta(
                days=1
            )
        except ValueError:
            date_to_dt = None

    # فلتر المشروع (إن وجد)
    project_id = None
    if filters["project_id"]:
        try:
            project_id = int(filters["project_id"])
        except ValueError:
            project_id = None

    # قائمة المشاريع
    projects = Project.query.order_by(Project.project_name.asc()).all()

    # كويري أساسي يُطبق عليه فلاتر المشروع والتاريخ
    base_q = PaymentRequest.query

    if project_id:
        base_q = base_q.filter(PaymentRequest.project_id == project_id)

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
