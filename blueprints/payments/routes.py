# blueprints/payments/routes.py

from datetime import datetime, timedelta, date

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    current_app,
    send_from_directory,
)
from flask_login import current_user
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import extract, false, exists, inspect, func
import os
import pathlib

from extensions import db
from permissions import role_required
from models import (
    PaymentRequest,
    Project,
    Supplier,
    PaymentApproval,
    PaymentAttachment,
    PaymentNotificationNote,
    user_projects,
)
from . import payments_bp


# تعريف ثوابت الحالات المستخدمة في النظام
STATUS_DRAFT = "draft"
STATUS_PENDING_PM = "pending_pm"
STATUS_PENDING_ENG = "pending_eng"
STATUS_PENDING_FIN = "pending_finance"
STATUS_READY_FOR_PAYMENT = "ready_for_payment"
STATUS_PAID = "paid"
STATUS_REJECTED = "rejected"

NOTIFIER_ALLOWED_STATUSES: set[str] = {
    STATUS_READY_FOR_PAYMENT,
    STATUS_PAID,
}

ALLOWED_STATUSES: set[str] = {
    STATUS_DRAFT,
    STATUS_PENDING_PM,
    STATUS_PENDING_ENG,
    STATUS_PENDING_FIN,
    STATUS_READY_FOR_PAYMENT,
    STATUS_PAID,
    STATUS_REJECTED,
}


# خريطة الانتقالات المسموح بها بين الحالات
# المفتاح: (الحالة_الحالية, الحالة_المطلوبة)
# القيمة: الأدوار التي يمكنها تنفيذ الانتقال
WORKFLOW_TRANSITIONS: dict[tuple[str, str], set[str]] = {
    (STATUS_DRAFT, STATUS_PENDING_PM): {
        "admin",
        "engineering_manager",
        "project_manager",
        "engineer",
    },
    (STATUS_PENDING_PM, STATUS_PENDING_ENG): {
        "admin",
        "engineering_manager",
        "project_manager",
    },
    (STATUS_PENDING_PM, STATUS_REJECTED): {
        "admin",
        "engineering_manager",
        "project_manager",
    },
    (STATUS_PENDING_ENG, STATUS_PENDING_FIN): {
        "admin",
        "engineering_manager",
    },
    (STATUS_PENDING_ENG, STATUS_REJECTED): {
        "admin",
        "engineering_manager",
    },
    (STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT): {
        "admin",
        "finance",
    },
    (STATUS_PENDING_FIN, STATUS_REJECTED): {
        "admin",
        "finance",
    },
    (STATUS_READY_FOR_PAYMENT, STATUS_PAID): {
        "admin",
        "finance",
    },
}


def _status_label(status: str) -> str:
    """إرجاع اسم الحالة باللغة الطبيعية لاستخدامه في الرسائل."""
    return PaymentRequest(status=status).human_status


# =========================
#   دوال مساعدة عامة
# =========================

def _get_role():
    if not current_user.is_authenticated or not current_user.role:
        return None
    return current_user.role.name


def _user_projects_table_exists() -> bool:
    try:
        inspector = inspect(db.engine)
        return inspector.has_table("user_projects")
    except Exception:
        # على بيئات الإنتاج القديمة قد لا يكون الجدول موجودًا أو تكون قاعدة البيانات غير مُهيأة بعد
        return False


def _project_manager_project_ids() -> list[int] | None:
    """Return project IDs for current project manager based on available schema."""
    if not current_user.is_authenticated:
        return None

    if _user_projects_table_exists():
        return [
            row.project_id
            for row in db.session.query(user_projects.c.project_id)
            .filter(user_projects.c.user_id == current_user.id)
            .all()
        ]

    if current_user.project_id:
        return [current_user.project_id]

    return []


def _safe_int_arg(name: str, default: int | None, *, min_value: int | None = None, max_value: int | None = None) -> int | None:
    """Safely parse integer query params with bounds and fallback."""

    raw_value = request.args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default

    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _safe_date_arg(name: str) -> datetime | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _iso_week_bounds(week_number: int, *, reference_year: int | None = None) -> tuple[datetime, datetime]:
    """Return start/end datetimes (UTC naive) for the ISO week in the given year."""

    if reference_year is None:
        reference_year = datetime.utcnow().isocalendar().year

    start_date = date.fromisocalendar(reference_year, week_number, 1)
    end_date = start_date + timedelta(days=7)

    return (
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date, datetime.min.time()),
    )


def _can_view_payment(p: PaymentRequest) -> bool:
    role_name = _get_role()
    if role_name is None:
        return False

    if role_name == "payment_notifier":
        return p.status in NOTIFIER_ALLOWED_STATUSES

    # admin + المدير الهندسي + رئيس مجلس الإدارة يشوفوا الكل
    if role_name in ("admin", "engineering_manager", "chairman"):
        return True

    # المالية تشوف كل الدفعات
    if role_name == "finance":
        return True

    # مدير المشروع يشوف فقط دفعات مشاريعه المرتبطة
    if role_name == "project_manager":
        pm_project_ids = _project_manager_project_ids()
        if pm_project_ids is None:
            return False
        return p.project_id in pm_project_ids

    # المهندس يشوف فقط الدفعات التي أنشأها (لا نقوم بتوسيع الصلاحيات هنا)
    if role_name == "engineer":
        return p.created_by == current_user.id

    # DC حالياً لا يشوف الدفعات
    if role_name == "dc":
        return False

    return False


def _require_transition(payment: PaymentRequest, target_status: str) -> bool:
    """
    حارس موحد لانتقالات حالة الدفعات:
    - يتحقق من دور المستخدم الحالي
    - يتحقق من أن الانتقال (الحالة الحالية -> المطلوبة) مسموح به
    يعيد True إذا مسموح، False مع رسالة تحذير وإيقاف العملية إذا غير مسموح.
    """
    role_name = _get_role()
    allowed_roles = WORKFLOW_TRANSITIONS.get((payment.status, target_status))

    if role_name is None or allowed_roles is None or role_name not in allowed_roles:
        flash(
            "غير مسموح بتغيير حالة الدفعة من "
            f"({payment.human_status}) إلى ({_status_label(target_status)}) "
            "للدور الحالي.",
            "danger",
        )
        return False

    return True


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
        abort(404)


def _get_payment_or_404(payment_id: int, *, options: list | None = None) -> PaymentRequest:
    query = PaymentRequest.query
    if options:
        query = query.options(*options)
    payment = query.filter(PaymentRequest.id == payment_id).first()
    if payment is None:
        abort(404)
    if not _can_view_payment(payment):
        abort(404)

    return payment


def _attachments_base_path() -> str:
    return os.path.join(current_app.instance_path, "attachments")


def _attachments_enabled() -> bool:
    return bool(current_app.config.get("ATTACHMENTS_ENABLED"))


def _attachment_file_path(attachment: PaymentAttachment) -> pathlib.Path:
    stored = (attachment.stored_filename or "").strip()
    if not stored or os.path.basename(stored) != stored:
        abort(404)

    base_path = pathlib.Path(_attachments_base_path())
    return base_path / stored


def _remove_attachment_file(attachment: PaymentAttachment) -> None:
    """Best-effort removal of the attachment file without raising."""

    try:
        path = _attachment_file_path(attachment)
    except Exception:
        return

    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except Exception:
        # Ignore cleanup failures to avoid interrupting primary flow
        return


def _require_can_edit(p: PaymentRequest):
    if not _can_edit_payment(p):
        abort(403)


def _require_can_delete(p: PaymentRequest):
    if not _can_delete_payment(p):
        abort(403)


def _add_approval_log(
    payment: PaymentRequest,
    step: str,
    action: str,
    old_status: str,
    new_status: str,
    comment: str | None = None,
):
    """
    تسجيل حركة اعتماد / رفض في جدول PaymentApproval
    step: engineer, pm, eng_manager, finance
    action: submit, approve, reject, mark_paid, ...
    """
    log = PaymentApproval(
        payment_request_id=payment.id,
        step=step,
        action=action,
        old_status=old_status,
        new_status=new_status,
        decided_by_id=current_user.id if current_user.is_authenticated else None,
        decided_at=datetime.utcnow(),
        comment=comment,
    )
    db.session.add(log)


def _get_filter_lists():
    """
    ترجع القوائم المستخدمة في فلاتر قائمة الدفعات:
    - projects: كل المشاريع
    - request_types: أنواع الدفعات المميزة
    - status_choices: قائمة الحالات (value, label)
    """
    projects = Project.query.order_by(Project.project_name.asc()).all()

    # أنواع الدفعات المميزة من جدول الدفعات
    rt_rows = (
        db.session.query(PaymentRequest.request_type)
        .distinct()
        .order_by(PaymentRequest.request_type.asc())
        .all()
    )
    request_types = [r[0] for r in rt_rows if r[0]]

    status_choices = [
        ("", "الكل"),
        (STATUS_DRAFT, "مسودة (مدخل بواسطة المهندس)"),
        (STATUS_PENDING_PM, "تحت مراجعة مدير المشروع"),
        (STATUS_PENDING_ENG, "تحت مراجعة الإدارة الهندسية"),
        (STATUS_PENDING_FIN, "في انتظار اعتماد المالية"),
        (STATUS_READY_FOR_PAYMENT, "جاهزة للصرف"),
        (STATUS_PAID, "تم الصرف"),
        (STATUS_REJECTED, "مرفوضة"),
    ]

    return projects, request_types, status_choices


# =========================
#   قوائم الدفعات
# =========================

@payments_bp.route("/")
@payments_bp.route("/my")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
    "payment_notifier",
    "dc",
)
def index():
    """
    قائمة "دفعات حسب صلاحياتي" مع فلاتر:
    - المشروع
    - نوع الدفعة
    - الحالة
    - رقم الأسبوع (أسبوع الإرسال لمدير المشروع)
    - من تاريخ / إلى تاريخ
    """

    role_name = _get_role()
    pm_project_ids: list[int] | None = None

    q = PaymentRequest.query.options(
        selectinload(PaymentRequest.project),
        selectinload(PaymentRequest.supplier),
        selectinload(PaymentRequest.creator),
    )

    projects, request_types, status_choices = _get_filter_lists()
    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}

    if role_name == "payment_notifier":
        status_choices = [
            choice
            for choice in status_choices
            if choice[0] in ("", *NOTIFIER_ALLOWED_STATUSES)
        ]

    # صلاحيات العرض الأساسية
    if role_name in ("admin", "engineering_manager", "chairman", "finance"):
        pass
    elif role_name == "payment_notifier":
        q = q.filter(PaymentRequest.status.in_(NOTIFIER_ALLOWED_STATUSES))
    elif role_name == "project_manager":
        pm_project_ids = _project_manager_project_ids()
        if pm_project_ids:
            q = q.filter(PaymentRequest.project_id.in_(pm_project_ids))
        else:
            q = q.filter(false())
    elif role_name == "engineer":
        q = q.filter(PaymentRequest.created_by == current_user.id)
    elif role_name == "dc":
        q = q.filter(false())
    else:
        q = q.filter(false())

    filters = {"project_id": "", "request_type": "", "status": "", "week_number": "", "date_from": "", "date_to": ""}

    project_id = _safe_int_arg("project_id", None, min_value=1)
    if project_id:
        filters["project_id"] = str(project_id)
        if role_name == "project_manager":
            allowed_pm_projects = set(pm_project_ids or [])
            if project_id not in allowed_pm_projects:
                q = q.filter(false())
            else:
                q = q.filter(PaymentRequest.project_id == project_id)
        else:
            q = q.filter(PaymentRequest.project_id == project_id)

    raw_request_type = (request.args.get("request_type") or "").strip()
    if raw_request_type and raw_request_type in allowed_request_types:
        filters["request_type"] = raw_request_type
        q = q.filter(PaymentRequest.request_type == raw_request_type)

    status_filter = (request.args.get("status") or "").strip()
    if status_filter in ALLOWED_STATUSES:
        filters["status"] = status_filter
        q = q.filter(PaymentRequest.status == status_filter)
        if role_name == "payment_notifier" and status_filter not in NOTIFIER_ALLOWED_STATUSES:
            q = q.filter(false())

    raw_week = (request.args.get("week_number") or "").strip()
    week_number: int | None = None
    if raw_week:
        try:
            parsed_week = int(raw_week)
            if 1 <= parsed_week <= 53:
                week_number = parsed_week
        except (TypeError, ValueError):
            pass

    if week_number is not None:
        filters["week_number"] = str(week_number)
        reference_year = datetime.utcnow().isocalendar().year
        submission_ts = func.coalesce(
            PaymentRequest.submitted_to_pm_at, PaymentRequest.created_at
        )

        if db.session.get_bind().dialect.name == "sqlite":
            try:
                week_start, week_end = _iso_week_bounds(
                    week_number, reference_year=reference_year
                )
            except ValueError:
                week_start = week_end = None

            if week_start and week_end:
                q = q.filter(
                    submission_ts >= week_start,
                    submission_ts < week_end,
                )
        else:
            q = q.filter(
                extract("isoyear", submission_ts) == reference_year,
                extract("week", submission_ts) == week_number,
            )

    date_from_dt = _safe_date_arg("date_from")
    if date_from_dt:
        filters["date_from"] = date_from_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at >= date_from_dt)

    date_to_dt = _safe_date_arg("date_to")
    if date_to_dt:
        filters["date_to"] = date_to_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at < date_to_dt + timedelta(days=1))

    page = _safe_int_arg("page", 1, min_value=1) or 1
    per_page = _safe_int_arg("per_page", 20, min_value=1, max_value=100) or 20

    total_count = (
        q.order_by(None)
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )

    payments_query = q.order_by(
        PaymentRequest.created_at.desc(), PaymentRequest.id.desc()
    )
    pagination = payments_query.paginate(
        page=page, per_page=per_page, error_out=False, count=False
    )
    pagination.total = total_count
    payments = pagination.items

    query_params = {k: v for k, v in filters.items() if v}
    query_params["page"] = page
    query_params["per_page"] = per_page

    return render_template(
        "payments/list.html",
        payments=payments,
        pagination=pagination,
        query_params=query_params,
        page_title="دفعات حسب صلاحياتي",
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
    )


@payments_bp.route("/all")
@role_required("admin", "engineering_manager", "chairman")
def list_all():
    payments = (
        PaymentRequest.query.options(
            joinedload(PaymentRequest.project),
            joinedload(PaymentRequest.supplier),
            joinedload(PaymentRequest.creator),
        )
        .order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
        .all()
    )

    projects, request_types, status_choices = _get_filter_lists()

    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="جميع الدفعات",
        filters={},
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
    )


@payments_bp.route("/pm_review")
@role_required("admin", "engineering_manager", "project_manager", "chairman")
def pm_review():
    payments = (
        PaymentRequest.query.options(
            joinedload(PaymentRequest.project),
            joinedload(PaymentRequest.supplier),
            joinedload(PaymentRequest.creator),
        )
        .filter(PaymentRequest.status == STATUS_PENDING_PM)
        .order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
        .all()
    )

    projects, request_types, status_choices = _get_filter_lists()

    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="دفعات في انتظار مراجعة مدير المشروع",
        filters={},
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
    )


@payments_bp.route("/eng_review")
@role_required("admin", "engineering_manager", "chairman")
def eng_review():
    payments = (
        PaymentRequest.query.options(
            joinedload(PaymentRequest.project),
            joinedload(PaymentRequest.supplier),
            joinedload(PaymentRequest.creator),
        )
        .filter(PaymentRequest.status == STATUS_PENDING_ENG)
        .order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
        .all()
    )

    projects, request_types, status_choices = _get_filter_lists()

    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="دفعات في انتظار الإدارة الهندسية",
        filters={},
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
    )


@payments_bp.route("/finance_review")
@role_required("admin", "engineering_manager", "finance", "chairman")
def list_finance_review():
    """
    قائمة الدفعات الخاصة بالإدارة المالية:
    - كل الدفعات في مرحلة:
        * في انتظار المالية
        * جاهزة للصرف
        * تم الصرف
    """
    payments = (
        PaymentRequest.query.options(
            joinedload(PaymentRequest.project),
            joinedload(PaymentRequest.supplier),
            joinedload(PaymentRequest.creator),
        )
        .filter(
            PaymentRequest.status.in_(
                [STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT, STATUS_PAID]
            )
        )
        .order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
        .all()
    )

    projects, request_types, status_choices = _get_filter_lists()

    return render_template(
        "payments/list.html",
        payments=payments,
        page_title="جميع دفعات المالية",
        filters={},
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
    )


@payments_bp.route("/finance_eng_approved")
@role_required(
    "admin",
    "engineering_manager",
    "finance",
    "chairman",
    "payment_notifier",
)
def finance_eng_approved():
    """
    قائمة الدفعات الجاهزة للصرف:
    - دفعات حالتها READY_FOR_PAYMENT (معتمدة ماليًا ولم تُسجل كـ تم الصرف)
    مع فلاتر على:
    - المشروع
    - المورد/المقاول
    - نوع الدفعة
    - من تاريخ / إلى تاريخ (تاريخ الإنشاء)
    """

    # كويري أساسي: دفعات حالتها جاهزة للصرف
    q = PaymentRequest.query.options(
        joinedload(PaymentRequest.project),
        joinedload(PaymentRequest.supplier),
        joinedload(PaymentRequest.creator),
    ).filter(PaymentRequest.status == STATUS_READY_FOR_PAYMENT)

    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    _, request_types, _ = _get_filter_lists()
    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}

    filters = {"project_id": "", "supplier_id": "", "request_type": "", "date_from": "", "date_to": ""}

    project_id = _safe_int_arg("project_id", None, min_value=1)
    if project_id:
        filters["project_id"] = str(project_id)
        q = q.filter(PaymentRequest.project_id == project_id)

    supplier_id = _safe_int_arg("supplier_id", None, min_value=1)
    if supplier_id:
        filters["supplier_id"] = str(supplier_id)
        q = q.filter(PaymentRequest.supplier_id == supplier_id)

    raw_request_type = (request.args.get("request_type") or "").strip()
    if raw_request_type and raw_request_type in allowed_request_types:
        filters["request_type"] = raw_request_type
        q = q.filter(PaymentRequest.request_type == raw_request_type)

    date_from_dt = _safe_date_arg("date_from")
    if date_from_dt:
        filters["date_from"] = date_from_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at >= date_from_dt)

    date_to_dt = _safe_date_arg("date_to")
    if date_to_dt:
        filters["date_to"] = date_to_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at < date_to_dt + timedelta(days=1))

    payments = q.order_by(
        PaymentRequest.created_at.desc(), PaymentRequest.id.desc()
    ).all()

    return render_template(
        "payments/finance_eng_approved.html",
        payments=payments,
        projects=projects,
        suppliers=suppliers,
        filters=filters,
        page_title="دفعات جاهزة للصرف",
    )


# =========================
#   إنشاء / تعديل / حذف
# =========================

@payments_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "project_manager", "engineer")
def create_payment():
    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    # يمكن استخدام نفس قائمة أنواع الدفعات إن احتجناها في القالب
    _, request_types, _ = _get_filter_lists()

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
        request_types=request_types,
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
    "payment_notifier",
)
def detail(payment_id):
    """
    صفحة تفاصيل الدفعة:
    - تعرض الدفعة + الـ approvals logs اللازمة لعرض
      اسم وتاريخ من اعتمد أو رفض في كل مرحلة.
    """
    payment = _get_payment_or_404(
        payment_id,
        options=[
            joinedload(PaymentRequest.project),
            joinedload(PaymentRequest.supplier),
            joinedload(PaymentRequest.creator),
            joinedload(PaymentRequest.approvals).joinedload(PaymentApproval.decided_by),
            joinedload(PaymentRequest.notification_notes).joinedload(
                PaymentNotificationNote.user
            ),
        ],
    )

    # آخر رفض (من أي مرحلة)
    rejection_log = (
        PaymentApproval.query.filter(
            PaymentApproval.payment_request_id == payment.id,
            PaymentApproval.action == "reject",
        )
        .order_by(PaymentApproval.decided_at.desc())
        .first()
    )

    def _latest_step(step: str, actions: list[str]):
        return (
            PaymentApproval.query.filter(
                PaymentApproval.payment_request_id == payment.id,
                PaymentApproval.step == step,
                PaymentApproval.action.in_(actions),
            )
            .order_by(PaymentApproval.decided_at.desc())
            .first()
        )

    pm_decision = _latest_step("pm", ["approve", "reject"])
    eng_decision = _latest_step("eng_manager", ["approve", "reject"])
    fin_decision = _latest_step("finance", ["approve", "reject"])
    finance_ready_log = _latest_step("finance", ["approve"])
    paid_log = _latest_step("finance", ["mark_paid"])

    return render_template(
        "payments/detail.html",
        payment=payment,
        page_title=f"تفاصيل الدفعة رقم {payment.id}",
        rejection_log=rejection_log,
        pm_decision=pm_decision,
        eng_decision=eng_decision,
        fin_decision=fin_decision,
        finance_ready_log=finance_ready_log,
        paid_log=paid_log,
    )


@payments_bp.route("/<int:payment_id>/add_notification_note", methods=["POST"])
@role_required("payment_notifier")
def add_notification_note(payment_id: int):
    payment = _get_payment_or_404(payment_id)

    if _get_role() == "payment_notifier" and payment.status not in NOTIFIER_ALLOWED_STATUSES:
        abort(404)

    note_text = (request.form.get("note") or "").strip()
    if not note_text:
        flash("برجاء إدخال الملاحظة أولًا.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    note = PaymentNotificationNote(
        payment_request_id=payment.id,
        user_id=current_user.id,
        note=note_text,
        created_at=datetime.utcnow(),
    )
    db.session.add(note)
    db.session.commit()

    flash("تم تسجيل ملاحظة الإشعار بنجاح.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/attachments/<int:attachment_id>/download")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
    "payment_notifier",
)
def download_attachment(attachment_id: int):
    attachment = PaymentAttachment.query.get_or_404(attachment_id)
    payment = _get_payment_or_404(attachment.payment_request_id)

    if not _attachments_enabled():
        flash("تم تعطيل تحميل المرفقات حالياً.", "warning")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    file_path = _attachment_file_path(attachment)

    if not file_path.is_file():
        flash(
            "الملف المطلوب غير موجود على الخادم، برجاء إعادة رفعه أو التواصل مع الدعم.",
            "warning",
        )
        abort(404)

    return send_from_directory(
        str(file_path.parent),
        file_path.name,
        as_attachment=True,
        download_name=attachment.original_filename,
    )


@payments_bp.route("/<int:payment_id>/edit", methods=["GET", "POST"])
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
)
def edit_payment(payment_id):
    payment = _get_payment_or_404(payment_id)
    _require_can_edit(payment)

    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    # هنا نجيب قائمة أنواع الدفعات ونرسلها للقالب
    _, request_types, _ = _get_filter_lists()

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
        request_types=request_types,
        page_title=f"تعديل الدفعة رقم {payment.id}",
    )


@payments_bp.route("/<int:payment_id>/delete", methods=["POST"])
@role_required("admin", "engineering_manager")
def delete_payment(payment_id):
    """
    عند حذف الدفعة:
    - نحذف أولاً كل سجلات الاعتماد PaymentApproval المرتبطة بها
    - ثم نحذف المرفقات PaymentAttachment
    - ثم نحذف الدفعة نفسها
    بذلك نتجنب محاولة تعيين payment_request_id = NULL (وهو NOT NULL).
    """
    payment = _get_payment_or_404(payment_id)
    _require_can_delete(payment)

    # حذف سجلات الاعتماد المرتبطة
    PaymentApproval.query.filter_by(
        payment_request_id=payment.id
    ).delete(synchronize_session=False)

    attachments = list(
        PaymentAttachment.query.filter_by(
            payment_request_id=payment.id
        ).all()
    )
    for att in attachments:
        _remove_attachment_file(att)

    # حذف المرفقات المرتبطة
    PaymentAttachment.query.filter_by(
        payment_request_id=payment.id
    ).delete(synchronize_session=False)

    # حذف الدفعة نفسها
    db.session.delete(payment)
    db.session.commit()

    flash(f"تم حذف الدفعة رقم {payment.id} بنجاح.", "success")
    return redirect(url_for("payments.index"))


# =========================
#   خطوات الـ Workflow
# =========================

@payments_bp.route("/<int:payment_id>/submit_to_pm", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager", "engineer")
def submit_to_pm(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_PM):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_PENDING_PM
    payment.updated_at = datetime.utcnow()
    payment.submitted_to_pm_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="engineer",
        action="submit",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم إرسال الدفعة إلى مدير المشروع للمراجعة.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/pm_approve", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_approve(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_ENG):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_PENDING_ENG
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="pm",
        action="approve",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم اعتماد الدفعة من مدير المشروع وتم إرسالها للإدارة الهندسية.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/pm_reject", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="pm",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم رفض الدفعة من مدير المشروع.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/eng_approve", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_approve(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_FIN):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_PENDING_FIN
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="eng_manager",
        action="approve",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم اعتماد الدفعة من الإدارة الهندسية وتم إرسالها للمالية.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/eng_reject", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="eng_manager",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم رفض الدفعة من الإدارة الهندسية.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/finance_approve", methods=["POST"])
@role_required("admin", "finance")
def finance_approve(payment_id):
    """
    موافقة المالية الأولى:
    - تتحول الحالة من pending_finance إلى ready_for_payment
    - لا نسجل مبلغ المالية الفعلي هنا (هيتسجل في خطوة تم الصرف)
    """
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_READY_FOR_PAYMENT):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_READY_FOR_PAYMENT
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="finance",
        action="approve",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم اعتماد الدفعة ماليًا وأصبحت جاهزة للصرف.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/finance_reject", methods=["POST"])
@role_required("admin", "finance")
def finance_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.status = STATUS_REJECTED
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="finance",
        action="reject",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم رفض الدفعة من المالية.", "danger")
    return redirect(url_for("payments.detail", payment_id=payment.id))


@payments_bp.route("/<int:payment_id>/mark_paid", methods=["POST"])
@role_required("admin", "finance")
def mark_paid(payment_id):
    """
    خطوة تم الصرف:
    - الحالة يجب أن تكون READY_FOR_PAYMENT
    - يُطلب من المالية إدخال amount_finance (المبلغ الفعلي المعتمد)
    - يتم حفظ amount_finance وتغيير الحالة إلى PAID
    """
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PAID):
        return redirect(url_for("payments.detail", payment_id=payment.id))

    amount_finance_str = (request.form.get("amount_finance") or "").strip()
    if not amount_finance_str:
        flash("برجاء إدخال مبلغ المالية الفعلي قبل تأكيد الصرف.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    try:
        amount_finance = float(amount_finance_str.replace(",", ""))
    except ValueError:
        flash("برجاء إدخال مبلغ مالية فعلي صحيح.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment.id))

    old_status = payment.status
    payment.amount_finance = amount_finance
    payment.status = STATUS_PAID
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="finance",
        action="mark_paid",
        old_status=old_status,
        new_status=payment.status,
    )

    db.session.commit()

    flash("تم تسجيل أن الدفعة تم صرفها وحفظ مبلغ المالية الفعلي.", "success")
    return redirect(url_for("payments.detail", payment_id=payment.id))
