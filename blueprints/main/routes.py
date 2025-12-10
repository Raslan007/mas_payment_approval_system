# blueprints/main/routes.py

from datetime import datetime, timedelta

from flask import redirect, url_for, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

from permissions import role_required
from . import main_bp
from models import PaymentRequest, Project, PaymentApproval

# نعرّف نفس قيم الحالات المستخدمة في payments.routes
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
    توجيه المستخدم إلى الصفحة الصحيحة بعد تسجيل الدخول
    بناءً على الدور الخاص به.
    """

    role_name = current_user.role.name if current_user.role else None

    # 1) المالية → دفعات معتمدة هندسياً في انتظار المالية
    if role_name == "finance":
        return redirect(url_for("payments.finance_eng_approved"))

    # 2) مدير مشروع → دفعاته حسب صلاحياته
    if role_name == "project_manager":
        return redirect(url_for("payments.index"))

    # 3) مهندس → دفعاته حسب صلاحياته
    if role_name == "engineer":
        return redirect(url_for("payments.index"))

    # 4) Data Entry (DC) → إدارة المستخدمين
    if role_name == "dc":
        return redirect(url_for("users.list_users"))

    # 5) رئيس مجلس الإدارة → جميع الدفعات (عرض فقط)
    if role_name == "chairman":
        return redirect(url_for("payments.list_all"))

    # 6) admin + المدير الهندسي + أي دور آخر → قائمة الدفعات الافتراضية
    return redirect(url_for("payments.index"))


@main_bp.route("/eng-dashboard")
@role_required("admin", "engineering_manager", "chairman")
def eng_dashboard():
    """
    لوحة الإدارة الهندسية:
    - متاحة فقط لـ admin + مدير الإدارة الهندسية + رئيس مجلس الإدارة.
    - يتم تمرير filters للمساعدة في حقول الفلترة بالقالب.
    - يتم حساب مؤشرات الأداء بناءً على نفس الفلاتر.
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
            # نضيف يوم كامل حتى يشمل اليوم بالكامل (<= نهاية اليوم)
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

    # قائمة المشاريع لاستخدامها في قائمة اختيار بالمخطط
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

    # دفعات في انتظار المالية (تم اعتمادها هندسياً وتم تمريرها للمالية)
    waiting_finance_q = base_q.filter(PaymentRequest.status == STATUS_PENDING_FIN)
    waiting_finance_count = waiting_finance_q.count()
    waiting_finance_total = (
        waiting_finance_q.with_entities(
            func.coalesce(func.sum(PaymentRequest.amount), 0.0)
        )
        .scalar()
        or 0.0
    )

    # مبالغ معتمدة من الإدارة الهندسية (أي حالة بعد المرور على الهندسية)
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

    # دفعات مرفوضة (نعتبرها مرفوضة هندسياً كإجمالي مبدئي)
    rejected_q = base_q.filter(PaymentRequest.status == STATUS_REJECTED)
    rejected_by_eng_count = rejected_q.count()
    rejected_by_eng_total = (
        rejected_q.with_entities(func.coalesce(func.sum(PaymentRequest.amount), 0.0))
        .scalar()
        or 0.0
    )

    # ---- توزيع حسب المشروع ----

    # تحت مراجعة الإدارة الهندسية حسب المشروع
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

    # في انتظار المالية حسب المشروع
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
    # نفترض أن step = 'eng_manager' في جدول PaymentApproval للقرارات الهندسية
    recent_eng_logs = (
        PaymentApproval.query.filter(PaymentApproval.step == "eng_manager")
        .order_by(PaymentApproval.decided_at.desc())
        .limit(10)
        .all()
    )

    # يمكن الاحتفاظ بإحصائيات إضافية عامة لو أحببت استخدامها لاحقاً
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
