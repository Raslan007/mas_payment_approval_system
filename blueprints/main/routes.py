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
    توجيه المستخدم إلى الصفحة الصحيحة بعد تسجيل الدخول
    بناءً على الدور الخاص به.
    """

    role_name = current_user.role.name if current_user.role else None

    if role_name == "finance":
        return redirect(url_for("payments.finance_eng_approved"))

    if role_name == "project_manager":
        return redirect(url_for("payments.index"))

    if role_name == "engineer":
        return redirect(url_for("payments.index"))

    if role_name == "dc":
        return redirect(url_for("users.list_users"))

    # admin + engineering_manager + chairman + finance
    if role_name in ("admin", "engineering_manager", "chairman", "finance"):
        return redirect(url_for("main.dashboard"))

    return redirect(url_for("payments.index"))


# -------------------------------------------------------------------
# لوحة التحكم العامة للدفعات  (تم إضافة finance هنا)
# -------------------------------------------------------------------
@main_bp.route("/dashboard")
@role_required("admin", "engineering_manager", "chairman", "finance")
def dashboard():
    """
    لوحة تحكم عامة للدفعات
    """

    base_q = PaymentRequest.query

    total_count = base_q.count()

    total_amount = (
        base_q.with_entities(func.coalesce(func.sum(PaymentRequest.amount), 0.0))
        .scalar()
        or 0.0
    )

    paid_q = base_q.filter(PaymentRequest.status == STATUS_PAID)
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

    waiting_fin_q = base_q.filter(PaymentRequest.status == STATUS_PENDING_FIN)
    total_waiting_finance = (
        waiting_fin_q.with_entities(
            func.coalesce(func.sum(PaymentRequest.amount), 0.0)
        )
        .scalar()
        or 0.0
    )

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
