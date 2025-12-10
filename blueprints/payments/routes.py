# blueprints/payments/routes.py

from datetime import datetime

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)
from flask_login import current_user
from extensions import db
from permissions import role_required
from models import PaymentRequest, Project, Supplier, PaymentApproval
from . import payments_bp


STATUS_DRAFT = "draft"
STATUS_PENDING_PM = "pending_pm"
STATUS_PENDING_ENG = "pending_eng"
STATUS_PENDING_FIN = "pending_finance"
STATUS_READY_FOR_PAYMENT = "ready_for_payment"
STATUS_PAID = "paid"
STATUS_REJECTED = "rejected"


def _get_role():
    if not current_user.is_authenticated or not current_user.role:
        return None
    return current_user.role.name


def _can_view_payment(p: PaymentRequest) -> bool:
    role_name = _get_role()
    if role_name is None:
        return False

    if role_name in ("admin", "engineering_manager", "chairman"):
        return True

    if role_name == "finance":
        return p.status in (
            STATUS_PENDING_FIN,
            STATUS_READY_FOR_PAYMENT,
            STATUS_PAID,
        )

    if role_name in ("engineer", "project_manager"):
        return p.created_by == current_user.id

    if role_name == "dc":
        return False

    return False


def _can_edit_payment(p: PaymentRequest) -> bool:
    role_name = _get_role()
    if role_name is None:
        return False

    if role_name in ("admin", "engineering_manager"):
        return True

    if role_name == "engineer":
        return p.created_by == current_user.id and p.status == STATUS_DRAFT

    if role_name == "project_manager":
        return p.created_by == current_user.id and p.status in (
            STATUS_DRAFT,
            STATUS_PENDING_PM,
        )

    return False


def _can_delete_payment(p: PaymentRequest) -> bool:
    """
    حذف الدفعة مسموح فقط لـ:
    - admin
    - engineering_manager
    ويمكن الحذف في أي حالة.
    """
    role_name = _get_role()
    if role_name is None:
        return False
    return role_name in ("admin", "engineering_manager")


def _require_can_view(p: PaymentRequest):
    if not _can_view_payment(p):
        abort(403)


def _require_can_edit(p: PaymentRequest):
    if not _can_edit_payment(p):
        abort(403)


def _require_can_delete(p: PaymentRequest):
    if not _can_delete_payment(p):
        abort(403)


@payments_bp.route("/")
@payments_bp.route("/my")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
)
def index():
    role_name = _get_role()
    q = PaymentRequest.query

    if role_name in ("engineer", "project_manager"):
        q = q.filter(PaymentRequest.created_by == current_user.id)
    elif role_name == "finance":
        q = q.filter(
            PaymentRequest.status.in_(
                [STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT, STATUS_PAID]
            )
        )

    payments = q.order_by(PaymentRequest.id.desc()).all()
    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="دفعات حسب صلاحياتي",
    )


@payments_bp.route("/all")
@role_required("admin", "engineering_manager", "chairman")
def list_all():
    payments = PaymentRequest.query.order_by(PaymentRequest.id.desc()).all()
    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="جميع الدفعات",
    )


@payments_bp.route("/pm_review")
@role_required("admin", "engineering_manager", "project_manager", "chairman")
def pm_review():
    payments = (
        PaymentRequest.query.filter(
            PaymentRequest.status == STATUS_PENDING_PM
        )
        .order_by(PaymentRequest.id.desc())
        .all()
    )
    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="دفعات في انتظار مراجعة مدير المشروع",
    )


@payments_bp.route("/eng_review")
@role_required("admin", "engineering_manager", "chairman")
def eng_review():
    payments = (
        PaymentRequest.query.filter(
            PaymentRequest.status == STATUS_PENDING_ENG
        )
        .order_by(PaymentRequest.id.desc())
        .all()
    )
    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="دفعات في انتظار الإدارة الهندسية",
    )


@payments_bp.route("/finance_review")
@role_required("admin", "engineering_manager", "finance", "chairman")
def list_finance_review():
    """
    قائمة الدفعات الخاصة بالإدارة المالية:
    - تظهر كل الدفعات التي في مرحلة:
        * في انتظار المالية
        * جاهزة للصرف
        * تم الصرف
    """
    payments = (
        PaymentRequest.query.filter(
            PaymentRequest.status.in_(
                [STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT, STATUS_PAID]
            )
        )
        .order_by(PaymentRequest.id.desc())
        .all()
    )

    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="جميع دفعات المالية",
    )


@payments_bp.route("/finance_eng_approved")
@role_required("admin", "engineering_manager", "finance", "chairman")
def finance_eng_approved():
    q = PaymentRequest.query.filter(
        PaymentRequest.status == STATUS_PENDING_FIN
    )
    payments = q.order_by(PaymentRequest.id.desc()).all()

    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    filters = {
        "project_id": request.args.get("project_id"),
        "supplier_id": request.args.get("supplier_id"),
        "request_type": request.args.get("request_type"),
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
    }

    return render_template(
        "payments/finance_eng_approved.html",
        payments=payments,
        projects=projects,
        suppliers=suppliers,
        filters=filters,
        page_title="دفعات معتمدة من الإدارة الهندسية في انتظار المالية",
    )


@payments_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "project_manager", "engineer")
def create_payment():
    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    if request.method == "POST":
        project_id = request.form.get("project_id")
        supplier_id = request.form.get("supplier_id")
        request_type = (request.form.get("request_type") or "").strip()
        amount_str = (request.form.get("amount") or "").strip()
        description = (request.form.get("description") or "").strip()

        if not project_id or not supplier_id or not request_type or not amount_str:
            flash("من فضلك أدخل جميع البيانات الأساسية للدفعة.", "danger")
            return redirect(url_for("payments.create_payment"))

        try:
            amount = float(amount_str.replace(",", ""))
        except ValueError:
            flash("برجاء إدخال مبلغ صحيح.", "danger")
            return redirect(url_for("payments.create_payment"))

        payment = PaymentRequest(
            project_id=int(project_id),
            supplier_id=int(supplier_id),
            request_type=request_type,
            amount=amount,
            description=description,
            status=STATUS_DRAFT,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
        )

        db.session.add(payment)
        db.session.commit()

        flash("تم إنشاء طلب الدفعة كمسودة بنجاح.", "success")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    return render_template(
        "payments/create.html",
        projects=projects,
        suppliers=suppliers,
        page_title="إضافة دفعة جديدة",
    )


@payments_bp.route("/<int:payment_id>")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
)
def detail(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    # لو الدفعة مرفوضة، نجيب آخر حركة رفض من جدول PaymentApproval
    rejection_log = None
    if payment.status == STATUS_REJECTED:
        rejection_log = (
            PaymentApproval.query.filter_by(
                payment_request_id=payment.id,
                action="reject",
            )
            .order_by(PaymentApproval.decided_at.desc())
            .first()
        )

    return render_template(
        "payments/detail.html",
        payment=payment,
        rejection_log=rejection_log,
        page_title=f"تفاصيل الدفعة رقم {payment.id}",
    )


@payments_bp.route("/<int:payment_id>/edit", methods=["GET", "POST"])
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
)
def edit_payment(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_edit(payment)

    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()

    if request.method == "POST":
        project_id = request.form.get("project_id")
        supplier_id = request.form.get("supplier_id")
        request_type = (request.form.get("request_type") or "").strip()
        amount_str = (request.form.get("amount") or "").strip()
        description = (request.form.get("description") or "").strip()

        if not project_id or not supplier_id or not request_type or not amount_str:
            flash("من فضلك أدخل جميع البيانات الأساسية للدفعة.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        try:
            amount = float(amount_str.replace(",", ""))
        except ValueError:
            flash("برجاء إدخال مبلغ صحيح.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        payment.project_id = int(project_id)
        payment.supplier_id = int(supplier_id)
        payment.request_type = request_type
        payment.amount = amount
        payment.description = description
        payment.updated_at = datetime.utcnow()

        db.session.commit()
        flash("تم تحديث بيانات الدفعة بنجاح.", "success")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    return render_template(
        "payments/edit.html",
        payment=payment,
        projects=projects,
        suppliers=suppliers,
        page_title=f"تعديل الدفعة رقم {payment.id}",
    )


@payments_bp.route("/<int:payment_id>/delete", methods=["POST"])
@role_required("admin", "engineering_manager")
def delete_payment(payment_id):
    """
    حذف الدفعة:
    - مسموح لـ admin و engineering_manager فقط.
    """
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_delete(payment)

    db.session.delete(payment)
    db.session.commit()

    flash(f"تم حذف الدفعة رقم {payment.id} بنجاح.", "success")
    return redirect(url_for("payments.index"))


@payments_bp.route("/<int:payment_id>/submit_to_pm", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager", "engineer")
def submit_to_pm(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_DRAFT:
        flash("لا يمكن إرسال دفعة ليست في حالة مسودة.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    payment.status = STATUS_PENDING_PM
    payment.updated_at = datetime.utcnow()
    db.session.commit()

    flash("تم إرسال الدفعة إلى مدير المشروع للمراجعة.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/pm_approve", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_approve(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_PM:
        flash("هذه الدفعة ليست في مرحلة مراجعة مدير المشروع.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    payment.status = STATUS_PENDING_ENG
    payment.updated_at = datetime.utcnow()
    db.session.commit()

    flash("تم اعتماد الدفعة من مدير المشروع وتم إرسالها للإدارة الهندسية.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/pm_reject", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_reject(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_PM:
        flash("هذه الدفعة ليست في مرحلة مراجعة مدير المشروع.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    # تسجيل حركة الرفض في جدول PaymentApproval
    approval = PaymentApproval(
        payment_request_id=payment.id,
        step="pm",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
        decided_by_id=current_user.id,
    )
    db.session.add(approval)

    db.session.commit()

    flash("تم رفض الدفعة من مدير المشروع.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/eng_approve", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_approve(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_ENG:
        flash("هذه الدفعة ليست في مرحلة الإدارة الهندسية.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    payment.status = STATUS_PENDING_FIN
    payment.updated_at = datetime.utcnow()
    db.session.commit()

    flash("تم اعتماد الدفعة من الإدارة الهندسية وتم إرسالها للمالية.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/eng_reject", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_reject(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_ENG:
        flash("هذه الدفعة ليست في مرحلة الإدارة الهندسية.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    approval = PaymentApproval(
        payment_request_id=payment.id,
        step="eng_manager",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
        decided_by_id=current_user.id,
    )
    db.session.add(approval)

    db.session.commit()

    flash("تم رفض الدفعة من الإدارة الهندسية.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/finance_approve", methods=["POST"])
@role_required("admin", "finance")
def finance_approve(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_FIN:
        flash("هذه الدفعة ليست في مرحلة المالية.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    payment.status = STATUS_READY_FOR_PAYMENT
    payment.updated_at = datetime.utcnow()
    db.session.commit()

    flash("تم اعتماد الدفعة ماليًا وأصبحت جاهزة للصرف.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/finance_reject", methods=["POST"])
@role_required("admin", "finance")
def finance_reject(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_PENDING_FIN:
        flash("هذه الدفعة ليست في مرحلة المالية.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    approval = PaymentApproval(
        payment_request_id=payment.id,
        step="finance",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
        decided_by_id=current_user.id,
    )
    db.session.add(approval)

    db.session.commit()

    flash("تم رفض الدفعة من المالية.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/mark_paid", methods=["POST"])
@role_required("admin", "finance")
def mark_paid(payment_id):
    payment = PaymentRequest.query.get_or_404(payment_id)
    _require_can_view(payment)

    if payment.status != STATUS_READY_FOR_PAYMENT:
        flash("لا يمكن تحديد الدفعة كـ (تم الصرف) إلا بعد اعتمادها ماليًا.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    payment.status = STATUS_PAID
    payment.updated_at = datetime.utcnow()
    db.session.commit()

    flash("تم تسجيل أن الدفعة تم صرفها.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))
