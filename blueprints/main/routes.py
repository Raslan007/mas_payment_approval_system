# blueprints/main/routes.py

from flask import redirect, url_for, render_template, request
from flask_login import login_required, current_user
from permissions import role_required
from . import main_bp
from models import PaymentRequest, Project

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
    """

    # قراءة الفلاتر من الـ Query String (لو موجودة)
    filters = {
        "date_from": request.args.get("date_from") or "",
        "date_to": request.args.get("date_to") or "",
        "project_id": request.args.get("project_id") or "",
        "status": request.args.get("status") or "",
    }

    # قائمة المشاريع لاستخدامها في قائمة اختيار بالمخطط
    projects = Project.query.order_by(Project.project_name.asc()).all()

    # إحصائيات سريعة عن حالات الدفعات
    stats = {
        "total": PaymentRequest.query.count(),
        "draft": PaymentRequest.query.filter_by(status=STATUS_DRAFT).count(),
        "pending_pm": PaymentRequest.query.filter_by(status=STATUS_PENDING_PM).count(),
        "pending_eng": PaymentRequest.query.filter_by(status=STATUS_PENDING_ENG).count(),
        "pending_finance": PaymentRequest.query.filter_by(status=STATUS_PENDING_FIN).count(),
        "ready_for_payment": PaymentRequest.query.filter_by(status=STATUS_READY_FOR_PAYMENT).count(),
        "paid": PaymentRequest.query.filter_by(status=STATUS_PAID).count(),
        "rejected": PaymentRequest.query.filter_by(status=STATUS_REJECTED).count(),
    }

    return render_template(
        "eng_dashboard.html",
        page_title="لوحة الإدارة الهندسية",
        filters=filters,
        projects=projects,
        stats=stats,
    )
