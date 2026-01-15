# blueprints/payments/routes.py

import csv
import io
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
import math
import os
import pathlib
import logging
from urllib.parse import urljoin, urlparse

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    current_app,
    send_from_directory,
    Response,
    jsonify,
)
from flask_login import current_user
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import extract, false, exists, inspect, func

from extensions import db
from permissions import role_required, is_finance_user
from models import (
    PaymentRequest,
    Project,
    Supplier,
    PaymentApproval,
    PaymentAttachment,
    PaymentNotificationNote,
    PaymentFinanceAdjustment,
    Notification,
    Role,
    User,
    SavedView,
    user_projects,
)
from project_scopes import get_scoped_project_ids, project_access_allowed
from . import payments_bp
from .inbox_queries import (
    READY_FOR_PAYMENT_ROLES,
    build_action_required_query,
    build_overdue_query,
    build_ready_for_payment_query,
    scoped_inbox_base_query,
)

logger = logging.getLogger(__name__)


# تعريف ثوابت الحالات المستخدمة في النظام
STATUS_DRAFT = "draft"
STATUS_PENDING_PM = "pending_pm"
STATUS_PENDING_ENG = "pending_eng"
STATUS_PENDING_FIN = "pending_finance"
STATUS_READY_FOR_PAYMENT = "ready_for_payment"
STATUS_PAID = "paid"
STATUS_REJECTED = "rejected"
FINANCE_AMOUNT_EDITABLE_STATUSES: set[str] = {
    STATUS_PENDING_FIN,
    "waiting_finance",
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

EXPORT_ROW_LIMIT = 10000

ALLOWED_SAVED_VIEW_ENDPOINTS: set[str] = {
    "payments.index",
    "payments.list_all",
    "payments.pm_review",
    "payments.eng_review",
    "payments.list_finance_review",
    "payments.finance_eng_approved",
    "finance.workbench",
}

SAVED_VIEWS_ROLES: tuple[str, ...] = (
    "admin",
    "engineering_manager",
    "planning",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
    "payment_notifier",
    "dc",
)

PAYMENT_RELATION_OPTIONS = (
    selectinload(PaymentRequest.project),
    selectinload(PaymentRequest.supplier),
    selectinload(PaymentRequest.creator),
)

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
    role_name = current_user.role.name
    if role_name == "project_engineer":
        return "engineer"
    return role_name


def _normalize_return_to(target: str | None) -> str | None:
    if not target:
        return None
    target = target.strip()
    if target.endswith("?"):
        target = target[:-1]
    return target


def _is_safe_return_to(target: str | None) -> bool:
    if not target:
        return False

    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in ("http", "https")
        and ref_url.netloc == test_url.netloc
    )


def _get_return_to(default_endpoint: str = "payments.index", **default_kwargs) -> str:
    for candidate in (request.values.get("return_to"), request.referrer):
        normalized = _normalize_return_to(candidate)
        if normalized and _is_safe_return_to(normalized):
            return normalized

    return url_for(default_endpoint, **default_kwargs)


def _redirect_with_return_to(default_endpoint: str = "payments.index", **default_kwargs):
    return redirect(_get_return_to(default_endpoint, **default_kwargs))


def _parse_decimal_amount(raw_value: str | None) -> Decimal | None:
    if raw_value is None:
        return None
    raw_value = raw_value.strip()
    if not raw_value:
        return None
    try:
        value = Decimal(raw_value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if not value.is_finite():
        return None
    return value


def _user_projects_table_exists() -> bool:
    try:
        inspector = inspect(db.engine)
        return inspector.has_table("user_projects")
    except Exception:
        # على بيئات الإنتاج القديمة قد لا يكون الجدول موجودًا أو تكون قاعدة البيانات غير مُهيأة بعد
        return False


def _users_with_role(role_name: str) -> list[User]:
    return (
        User.query.join(Role)
        .filter(Role.name == role_name)
        .all()
    )


def _filter_project_scoped_users(
    users: list[User],
    project_id: int,
    role_name: str,
) -> list[User]:
    scoped: list[User] = []
    for user in users:
        if project_access_allowed(user, project_id, role_name=role_name):
            scoped.append(user)
            continue
        if getattr(user, "project_id", None) == project_id:
            scoped.append(user)
    return scoped


def _notification_recipients(
    payment: PaymentRequest,
    roles: tuple[str, ...],
    *,
    include_creator: bool = True,
) -> list[User]:
    recipient_ids: set[int] = set()
    recipients: list[User] = []

    if include_creator and payment.created_by:
        creator = db.session.get(User, payment.created_by)
        if creator:
            recipient_ids.add(creator.id)
            recipients.append(creator)

    admin_users = _users_with_role("admin")
    for user in admin_users:
        if user.id in recipient_ids:
            continue
        recipient_ids.add(user.id)
        recipients.append(user)

    for role_name in roles:
        role_users = _users_with_role(role_name)
        if role_name in {"project_manager", "engineer", "project_engineer"}:
            role_users = _filter_project_scoped_users(role_users, payment.project_id, role_name)
        for user in role_users:
            if user.id in recipient_ids:
                continue
            recipient_ids.add(user.id)
            recipients.append(user)

    return recipients


def _create_notifications(
    payment: PaymentRequest,
    *,
    title: str,
    message: str,
    url: str | None,
    roles: tuple[str, ...] = (),
    include_creator: bool = True,
) -> None:
    db.session.flush()
    recipients = _notification_recipients(
        payment,
        roles,
        include_creator=include_creator,
    )
    if not recipients:
        logger.info(
            "No notification recipients resolved for payment %s (%s).",
            payment.id,
            title,
        )
        return

    notifications = [
        Notification(
            user_id=user.id,
            title=title,
            message=message,
            url=url,
        )
        for user in recipients
    ]
    db.session.add_all(notifications)
    logger.info(
        "Created %s notifications for payment %s (%s) recipients=%s",
        len(notifications),
        payment.id,
        title,
        [user.id for user in recipients],
    )


def _project_manager_project_ids() -> list[int] | None:
    """Return project IDs for current project manager based on available schema."""
    if not current_user.is_authenticated:
        return None

    return get_scoped_project_ids(current_user, role_name="project_manager")


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


def _default_filters() -> dict[str, str]:
    return {
        "project_id": "",
        "request_type": "",
        "status": "",
        "status_group": "",
        "week_number": "",
        "date_from": "",
        "date_to": "",
    }


def _apply_filters(q, *, role_name: str | None, allowed_request_types: set[str], pm_project_ids: list[int] | None = None):
    filters = _default_filters()

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
    else:
        status_group = (request.args.get("status_group") or "").strip()
        if status_group in STATUS_GROUPS:
            filters["status_group"] = status_group
            q = q.filter(PaymentRequest.status.in_(STATUS_GROUPS[status_group]))

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

    return q, filters


def _paginate_payments_query(q, *, default_per_page: int = 20):
    page = _safe_int_arg("page", 1, min_value=1) or 1
    per_page = _safe_int_arg("per_page", default_per_page, min_value=1, max_value=100) or default_per_page

    total_count = (
        q.order_by(None)
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )

    ordered_q = q.order_by(
        PaymentRequest.created_at.desc(), PaymentRequest.id.desc()
    )
    pagination = ordered_q.paginate(
        page=page, per_page=per_page, error_out=False, count=False
    )
    pagination.total = total_count

    return pagination, page, per_page


def _render_inbox_list(q, *, page_title: str, filters: dict[str, str], pagination_endpoint: str):
    q = q.options(*PAYMENT_RELATION_OPTIONS)
    pagination, page, per_page = _paginate_payments_query(q)

    projects, request_types, status_choices = _get_filter_lists()

    query_params = {k: v for k, v in filters.items() if v}
    query_params["page"] = page
    query_params["per_page"] = per_page

    return render_template(
        "payments/list.html",
        payments=pagination.items,
        pagination=pagination,
        query_params=query_params,
        page_title=page_title,
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
        pagination_endpoint=pagination_endpoint,
        can_create_payment=_can_create_payment(),
        can_export_payments=False,
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


def _count_query(q):
    return (
        q.order_by(None)
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )


def _format_ts(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _export_query_to_csv(q, *, filename: str):
    total = _count_query(q)
    if total > EXPORT_ROW_LIMIT:
        message = (
            f"عدد النتائج ({total}) يتجاوز الحد الأقصى للتصدير ({EXPORT_ROW_LIMIT}). "
            "برجاء تضييق الفلاتر قبل التصدير."
        )
        return Response(
            message,
            status=400,
            mimetype="text/plain; charset=utf-8",
        )

    rows = (
        q.order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
        .limit(EXPORT_ROW_LIMIT)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "project",
            "project_code",
            "supplier",
            "supplier_type",
            "request_type",
            "status",
            "amount",
            "finance_amount",
            "progress_percentage",
            "created_at",
            "updated_at",
            "submitted_to_pm_at",
        ]
    )
    for payment in rows:
        writer.writerow(
            [
                payment.id,
                payment.project.project_name if payment.project else "",
                payment.project.code if payment.project else "",
                payment.supplier.name if payment.supplier else "",
                payment.supplier.supplier_type if payment.supplier else "",
                payment.request_type,
                payment.status,
                payment.amount,
                payment.finance_amount if payment.finance_amount is not None else "",
                payment.progress_percentage if payment.progress_percentage is not None else "",
                _format_ts(payment.created_at),
                _format_ts(payment.updated_at),
                _format_ts(payment.submitted_to_pm_at),
            ]
        )

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    if role_name in ("admin", "engineering_manager", "chairman", "planning"):
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

    # المهندس يشوف فقط دفعات مشاريعه المرتبطة أو التي أنشأها (في حال عدم وجود ربط متعدد)
    if role_name == "engineer":
        scoped_projects = get_scoped_project_ids(current_user, role_name="engineer")
        if scoped_projects:
            return p.project_id in scoped_projects
        return p.created_by == current_user.id

    # DC حالياً لا يشوف الدفعات
    if role_name == "dc":
        return False

    return False


def _can_create_payment() -> bool:
    role_name = _get_role()
    return role_name in ("admin", "engineering_manager", "project_manager", "engineer")


def _can_export_payments(allowed_roles: set[str]) -> bool:
    role_name = _get_role()
    return role_name in allowed_roles


def _can_take_action(p: PaymentRequest) -> bool:
    role_name = _get_role()
    if role_name is None:
        return False

    if _can_edit_payment(p) or _can_delete_payment(p):
        return True

    transition_targets = (
        STATUS_PENDING_PM,
        STATUS_PENDING_ENG,
        STATUS_PENDING_FIN,
        STATUS_READY_FOR_PAYMENT,
        STATUS_PAID,
        STATUS_REJECTED,
    )
    if any(_can_transition(p, target) for target in transition_targets):
        return True

    return (
        role_name == "finance"
        and p.status in FINANCE_AMOUNT_EDITABLE_STATUSES
    )


def _can_transition(payment: PaymentRequest, target_status: str) -> bool:
    role_name = _get_role()
    allowed_roles = WORKFLOW_TRANSITIONS.get((payment.status, target_status))
    if role_name is None or not allowed_roles:
        return False
    return role_name in allowed_roles


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

    if p.status in (
        STATUS_PENDING_FIN,
        STATUS_READY_FOR_PAYMENT,
        STATUS_PAID,
    ):
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
    ويمنع الحذف في الحالات الجاهزة أو المصروفة.
    """
    role_name = _get_role()
    if role_name is None:
        return False

    if p.status in (
        STATUS_READY_FOR_PAYMENT,
        STATUS_PAID,
    ):
        return False

    return role_name in ("admin", "engineering_manager")


def _clean_query_string(raw_query: str | None) -> str:
    if not raw_query:
        return ""
    return raw_query.lstrip("?").strip()


def _saved_view_allowed(endpoint: str) -> bool:
    return endpoint in ALLOWED_SAVED_VIEW_ENDPOINTS


def _get_user_saved_view_or_404(view_id: int) -> SavedView:
    view = SavedView.query.filter(
        SavedView.id == view_id,
        SavedView.user_id == current_user.id,
    ).first()
    if view is None:
        abort(404)
    return view


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
    if not stored:
        abort(404)

    if ".." in stored or "/" in stored or "\\" in stored:
        abort(404)

    if os.path.basename(stored) != stored:
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
#   العروض المحفوظة
# =========================


@payments_bp.route("/saved_views")
@role_required(*SAVED_VIEWS_ROLES)
def saved_views():
    views = (
        SavedView.query.filter(SavedView.user_id == current_user.id)
        .order_by(SavedView.created_at.desc(), SavedView.id.desc())
        .all()
    )
    return render_template(
        "payments/saved_views.html",
        saved_views=views,
        page_title="عروضي المحفوظة",
    )


@payments_bp.route("/saved_views/create", methods=["POST"])
@role_required(*SAVED_VIEWS_ROLES)
def create_saved_view():
    name = (request.form.get("name") or "").strip()
    endpoint = (request.form.get("endpoint") or "").strip()
    query_string = _clean_query_string(request.form.get("query_string"))
    requested_return_to = _normalize_return_to(request.form.get("return_to"))
    return_to = (
        requested_return_to
        if requested_return_to and _is_safe_return_to(requested_return_to)
        else _get_return_to()
    )

    if not name:
        flash("يرجى إدخال اسم صالح للعرض المحفوظ.", "danger")
        return redirect(return_to)

    if not _saved_view_allowed(endpoint):
        abort(400, description="Endpoint not allowed for saved views.")

    view = SavedView(
        user_id=current_user.id,
        name=name,
        endpoint=endpoint,
        query_string=query_string,
    )
    db.session.add(view)
    db.session.commit()
    flash("تم حفظ العرض الحالي.", "success")
    return redirect(return_to)


@payments_bp.route("/saved_views/<int:view_id>/delete", methods=["POST"])
@role_required(*SAVED_VIEWS_ROLES)
def delete_saved_view(view_id: int):
    view = _get_user_saved_view_or_404(view_id)
    requested_return_to = _normalize_return_to(request.form.get("return_to"))
    return_to = (
        requested_return_to
        if requested_return_to and _is_safe_return_to(requested_return_to)
        else url_for("payments.saved_views")
    )

    db.session.delete(view)
    db.session.commit()
    flash("تم حذف العرض المحفوظ.", "success")
    return redirect(return_to)


@payments_bp.route("/saved_views/<int:view_id>/open")
@role_required(*SAVED_VIEWS_ROLES)
def open_saved_view(view_id: int):
    view = _get_user_saved_view_or_404(view_id)

    if not _saved_view_allowed(view.endpoint):
        abort(400, description="Saved view endpoint is not allowed.")

    base_url = url_for(view.endpoint)
    query_string = _clean_query_string(view.query_string)
    target = f"{base_url}?{query_string}" if query_string else base_url

    if not _is_safe_return_to(target):
        abort(400, description="Unsafe redirect target.")

    return redirect(target)


def _scoped_payments_query_for_listing():
    role_name = _get_role()
    pm_project_ids: list[int] | None = None
    engineer_project_ids: list[int] | None = None

    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS)

    projects, request_types, status_choices = _get_filter_lists()
    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}

    if role_name == "payment_notifier":
        status_choices = [
            choice
            for choice in status_choices
            if choice[0] in ("", *NOTIFIER_ALLOWED_STATUSES)
        ]

    # صلاحيات العرض الأساسية
    if role_name in ("admin", "engineering_manager", "chairman", "finance", "planning"):
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
        engineer_project_ids = get_scoped_project_ids(current_user, role_name="engineer")
        if engineer_project_ids:
            q = q.filter(PaymentRequest.project_id.in_(engineer_project_ids))
        else:
            q = q.filter(PaymentRequest.created_by == current_user.id)
    elif role_name == "dc":
        q = q.filter(false())
    else:
        q = q.filter(false())

    q, filters = _apply_filters(
        q,
        role_name=role_name,
        allowed_request_types=allowed_request_types,
        pm_project_ids=pm_project_ids,
    )

    return q, filters, projects, request_types, status_choices


# =========================
#   قوائم الـ Inbox الجديدة
# =========================


@payments_bp.route("/inbox/action-required")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
)
def inbox_action_required():
    base_q, role_name, _ = scoped_inbox_base_query(current_user)
    inbox_q = build_action_required_query(base_q, role_name)
    filters = _default_filters()
    filters["status_group"] = "outstanding"

    return _render_inbox_list(
        inbox_q,
        page_title="دفعات مطلوبة لإجراء",
        filters=filters,
        pagination_endpoint="payments.inbox_action_required",
    )


@payments_bp.route("/inbox/overdue")
@role_required(
    "admin",
    "engineering_manager",
    "project_manager",
    "engineer",
    "finance",
    "chairman",
)
def inbox_overdue():
    base_q, _, _ = scoped_inbox_base_query(current_user)
    inbox_q = build_overdue_query(base_q, config=current_app.config)
    filters = _default_filters()
    filters["status_group"] = "outstanding"

    return _render_inbox_list(
        inbox_q,
        page_title="دفعات متأخرة عن SLA",
        filters=filters,
        pagination_endpoint="payments.inbox_overdue",
    )


@payments_bp.route("/inbox/ready-for-payment")
@role_required(*READY_FOR_PAYMENT_ROLES)
def inbox_ready_for_payment():
    base_q, role_name, _ = scoped_inbox_base_query(current_user)
    if role_name not in READY_FOR_PAYMENT_ROLES:
        abort(403)

    inbox_q = build_ready_for_payment_query(base_q)
    filters = _default_filters()
    filters["status"] = STATUS_READY_FOR_PAYMENT

    return _render_inbox_list(
        inbox_q,
        page_title="دفعات جاهزة للصرف",
        filters=filters,
        pagination_endpoint="payments.inbox_ready_for_payment",
    )


# =========================
#   قوائم الدفعات
# =========================

@payments_bp.route("/")
@payments_bp.route("/my")
@role_required(
    "admin",
    "engineering_manager",
    "planning",
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

    q, filters, projects, request_types, status_choices = _scoped_payments_query_for_listing()

    pagination, page, per_page = _paginate_payments_query(q)
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
        export_endpoint="payments.export_my",
        export_params={k: v for k, v in filters.items() if v},
        can_create_payment=_can_create_payment(),
        can_export_payments=_can_export_payments(
            {
                "admin",
                "engineering_manager",
                "project_manager",
                "engineer",
                "finance",
                "chairman",
                "payment_notifier",
                "dc",
            }
        ),
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


@payments_bp.route("/export")
@payments_bp.route("/my/export")
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
def export_my():
    q, filters, _, _, _ = _scoped_payments_query_for_listing()
    return _export_query_to_csv(q, filename="payments_export.csv")


@payments_bp.route("/all")
@role_required("admin", "engineering_manager", "chairman", "planning")
def list_all():
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS)

    projects, request_types, status_choices = _get_filter_lists()

    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}
    role_name = _get_role()

    q, filters = _apply_filters(
        q,
        role_name=role_name,
        allowed_request_types=allowed_request_types,
    )

    pagination, page, per_page = _paginate_payments_query(q)
    query_params = {k: v for k, v in filters.items() if v}
    query_params["page"] = page
    query_params["per_page"] = per_page

    return render_template(
        "payments/list.html",
        payments=pagination.items,
        pagination=pagination,
        query_params=query_params,
        page_title="جميع الدفعات",
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
        export_endpoint="payments.export_all",
        export_params={k: v for k, v in filters.items() if v},
        can_create_payment=_can_create_payment(),
        can_export_payments=_can_export_payments(
            {
                "admin",
                "engineering_manager",
                "chairman",
            }
        ),
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


@payments_bp.route("/all/export")
@role_required("admin", "engineering_manager", "chairman")
def export_all():
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS)

    _, request_types, _ = _get_filter_lists()
    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}
    role_name = _get_role()

    q, _ = _apply_filters(
        q,
        role_name=role_name,
        allowed_request_types=allowed_request_types,
    )

    return _export_query_to_csv(q, filename="payments_all_export.csv")


@payments_bp.route("/pm_review")
@role_required("admin", "engineering_manager", "project_manager", "chairman", "planning")
def pm_review():
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS).filter(PaymentRequest.status == STATUS_PENDING_PM)

    pagination, page, per_page = _paginate_payments_query(q)

    projects, request_types, status_choices = _get_filter_lists()
    filters = _default_filters()
    filters["status"] = STATUS_PENDING_PM
    query_params = {"page": page, "per_page": per_page, "status": STATUS_PENDING_PM}

    return render_template(
        "payments/list.html",
        payments=pagination.items,
        pagination=pagination,
        query_params=query_params,
        page_title="دفعات في انتظار مراجعة مدير المشروع",
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
        can_create_payment=_can_create_payment(),
        can_export_payments=False,
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


@payments_bp.route("/eng_review")
@role_required("admin", "engineering_manager", "chairman", "planning")
def eng_review():
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS).filter(PaymentRequest.status == STATUS_PENDING_ENG)

    pagination, page, per_page = _paginate_payments_query(q)

    projects, request_types, status_choices = _get_filter_lists()
    filters = _default_filters()
    filters["status"] = STATUS_PENDING_ENG
    query_params = {"page": page, "per_page": per_page, "status": STATUS_PENDING_ENG}

    return render_template(
        "payments/list.html",
        payments=pagination.items,
        pagination=pagination,
        query_params=query_params,
        page_title="دفعات في انتظار الإدارة الهندسية",
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
        can_create_payment=_can_create_payment(),
        can_export_payments=False,
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


@payments_bp.route("/finance_review")
@role_required("admin", "engineering_manager", "finance", "chairman", "planning")
def list_finance_review():
    """
    قائمة الدفعات الخاصة بالإدارة المالية:
    - كل الدفعات في مرحلة:
        * في انتظار المالية
        * جاهزة للصرف
        * تم الصرف
    """
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS).filter(
        PaymentRequest.status.in_(
            [STATUS_PENDING_FIN, STATUS_READY_FOR_PAYMENT, STATUS_PAID]
        )
    )

    pagination, page, per_page = _paginate_payments_query(q)

    projects, request_types, status_choices = _get_filter_lists()
    filters = _default_filters()
    query_params = {"page": page, "per_page": per_page}

    return render_template(
        "payments/list.html",
        payments=pagination.items,
        pagination=pagination,
        query_params=query_params,
        page_title="جميع دفعات المالية",
        filters=filters,
        projects=projects,
        request_types=request_types,
        status_choices=status_choices,
        can_create_payment=_can_create_payment(),
        can_export_payments=False,
        can_edit_payment=_can_edit_payment,
        can_take_action=_can_take_action,
    )


def _finance_ready_query():
    q = PaymentRequest.query.options(*PAYMENT_RELATION_OPTIONS).filter(PaymentRequest.status == STATUS_READY_FOR_PAYMENT)

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

    return q, filters, projects, suppliers


@payments_bp.route("/finance_eng_approved")
@role_required(
    "admin",
    "engineering_manager",
    "finance",
    "chairman",
    "payment_notifier",
    "planning",
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

    q, filters, projects, suppliers = _finance_ready_query()

    pagination, page, per_page = _paginate_payments_query(q)
    payments = pagination.items

    query_params = {k: v for k, v in filters.items() if v}
    query_params["page"] = page
    query_params["per_page"] = per_page

    return render_template(
        "payments/finance_eng_approved.html",
        payments=payments,
        pagination=pagination,
        query_params=query_params,
        projects=projects,
        suppliers=suppliers,
        filters=filters,
        pagination_endpoint="payments.finance_eng_approved",
        page_title="دفعات جاهزة للصرف",
        export_endpoint="payments.export_finance_ready",
        export_params={k: v for k, v in filters.items() if v},
    )


@payments_bp.route("/finance_eng_approved/export")
@role_required(
    "admin",
    "engineering_manager",
    "finance",
    "chairman",
    "payment_notifier",
)
def export_finance_ready():
    q, _, _, _ = _finance_ready_query()
    return _export_query_to_csv(q, filename="payments_finance_ready.csv")


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
            project_id_value = int(project_id)
            supplier_id_value = int(supplier_id)
        except (TypeError, ValueError):
            flash("برجاء اختيار مشروع ومورد صحيح.", "danger")
            return redirect(url_for("payments.create_payment"))

        project = db.session.get(Project, project_id_value)
        if project is None:
            flash("برجاء اختيار مشروع صحيح.", "danger")
            return redirect(url_for("payments.create_payment"))

        supplier = db.session.get(Supplier, supplier_id_value)
        if supplier is None:
            flash("برجاء اختيار مورد صحيح.", "danger")
            return redirect(url_for("payments.create_payment"))

        role_name = _get_role()
        if role_name == "engineer" and not project_access_allowed(
            current_user, project_id_value, role_name="engineer"
        ):
            abort(403)
        if role_name == "project_manager":
            pm_project_ids = _project_manager_project_ids() or []
            if project_id_value not in pm_project_ids:
                abort(403)
        if role_name not in ("admin", "engineering_manager", "project_manager", "engineer"):
            abort(403)

        try:
            amount = float(amount_str.replace(",", ""))
        except ValueError:
            flash("برجاء إدخال مبلغ صحيح.", "danger")
            return redirect(url_for("payments.create_payment"))

        if not math.isfinite(amount) or amount <= 0:
            flash("برجاء إدخال مبلغ صحيح أكبر من صفر.", "danger")
            return redirect(url_for("payments.create_payment"))

        payment = PaymentRequest(
            project_id=project.id,
            supplier_id=supplier.id,
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
    "planning",
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
            selectinload(PaymentRequest.finance_adjustments).joinedload(
                PaymentFinanceAdjustment.created_by
            ),
            selectinload(PaymentRequest.finance_adjustments).joinedload(
                PaymentFinanceAdjustment.voided_by
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

    return_to = _get_return_to()
    role_name = _get_role()

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
        return_to=return_to,
        can_edit=_can_edit_payment(payment),
        can_delete=_can_delete_payment(payment),
        can_submit_to_pm=_can_transition(payment, STATUS_PENDING_PM),
        can_pm_approve=_can_transition(payment, STATUS_PENDING_ENG),
        can_pm_reject=_can_transition(payment, STATUS_REJECTED),
        can_eng_approve=_can_transition(payment, STATUS_PENDING_FIN),
        can_eng_reject=_can_transition(payment, STATUS_REJECTED),
        can_fin_approve=_can_transition(payment, STATUS_READY_FOR_PAYMENT),
        can_fin_reject=_can_transition(payment, STATUS_REJECTED),
        can_mark_paid=_can_transition(payment, STATUS_PAID),
        can_add_notification_note=(
            role_name == "payment_notifier" and payment.status in NOTIFIER_ALLOWED_STATUSES
        ),
        can_update_finance_amount=(
            role_name == "finance"
            and payment.status in FINANCE_AMOUNT_EDITABLE_STATUSES
            and payment.finance_amount is None
        ),
        can_manage_finance_adjustments=is_finance_user(current_user),
        can_view_rejection_details=role_name in ("engineering_manager", "admin"),
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
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    note = PaymentNotificationNote(
        payment_request_id=payment.id,
        user_id=current_user.id,
        note=note_text,
        created_at=datetime.utcnow(),
    )
    db.session.add(note)

    _create_notifications(
        payment,
        title=f"ملاحظة إشعار للدفعة رقم {payment.id}",
        message=note_text,
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("finance", "project_manager"),
        include_creator=True,
    )
    db.session.commit()

    flash("تم تسجيل ملاحظة الإشعار بنجاح.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/finance-adjustments", methods=["POST"])
def create_finance_adjustment(payment_id: int):
    payment = _get_payment_or_404(payment_id)

    if not is_finance_user(current_user):
        abort(403)

    if payment.finance_amount is None:
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "base_amount_required"}), 400
        flash("يرجى تسجيل مبلغ المالية الأساسي قبل إضافة أي تصحيحات.", "warning")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    payload = request.get_json(silent=True) or {}
    raw_delta = payload.get("delta_amount") or request.form.get("delta_amount")
    reason = (payload.get("reason") or request.form.get("reason") or "").strip()
    notes = (payload.get("notes") or request.form.get("notes") or "").strip()

    delta_amount = _parse_decimal_amount(raw_delta)
    if delta_amount is None or delta_amount == 0:
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "invalid_delta_amount"}), 400
        flash("يرجى إدخال مبلغ تصحيح صالح وغير صفري.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    if not reason:
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "reason_required"}), 400
        flash("يرجى إدخال سبب التصحيح.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    adjustment = PaymentFinanceAdjustment(
        payment_id=payment.id,
        delta_amount=delta_amount,
        reason=reason,
        notes=notes or None,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(adjustment)
    db.session.commit()

    if request.is_json or request.accept_mimetypes.best == "application/json":
        return jsonify(
            {
                "ok": True,
                "adjustment": {
                    "id": adjustment.id,
                    "payment_id": adjustment.payment_id,
                    "delta_amount": str(adjustment.delta_amount),
                    "reason": adjustment.reason,
                    "notes": adjustment.notes,
                    "created_by_user_id": adjustment.created_by_user_id,
                    "created_at": adjustment.created_at.isoformat()
                    if adjustment.created_at
                    else None,
                    "is_void": adjustment.is_void,
                },
                "finance_effective_amount": str(payment.finance_effective_amount),
            }
        )

    flash("تم إضافة تصحيح الحسابات بنجاح.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route(
    "/<int:payment_id>/finance-adjustments/<int:adjustment_id>/void",
    methods=["POST"],
)
def void_finance_adjustment(payment_id: int, adjustment_id: int):
    payment = _get_payment_or_404(payment_id)

    if not is_finance_user(current_user):
        abort(403)

    adjustment = PaymentFinanceAdjustment.query.filter(
        PaymentFinanceAdjustment.id == adjustment_id,
        PaymentFinanceAdjustment.payment_id == payment.id,
    ).first()
    if adjustment is None:
        abort(404)

    if adjustment.is_void:
        flash("هذا التصحيح تم إلغاؤه مسبقاً.", "warning")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    payload = request.get_json(silent=True) or {}
    void_reason = (payload.get("void_reason") or request.form.get("void_reason") or "").strip()
    if not void_reason:
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "void_reason_required"}), 400
        flash("يرجى إدخال سبب الإلغاء.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    adjustment.is_void = True
    adjustment.void_reason = void_reason
    adjustment.voided_by_user_id = current_user.id
    adjustment.voided_at = datetime.utcnow()
    db.session.commit()

    if request.is_json or request.accept_mimetypes.best == "application/json":
        return jsonify(
            {
                "ok": True,
                "adjustment": {
                    "id": adjustment.id,
                    "payment_id": adjustment.payment_id,
                    "delta_amount": str(adjustment.delta_amount),
                    "reason": adjustment.reason,
                    "void_reason": adjustment.void_reason,
                    "is_void": adjustment.is_void,
                },
                "finance_effective_amount": str(payment.finance_effective_amount),
            }
        )

    flash("تم إلغاء التصحيح بنجاح.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/attachments/<int:attachment_id>/download")
@role_required(
    "admin",
    "engineering_manager",
    "planning",
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
            project_id_value = int(project_id)
            supplier_id_value = int(supplier_id)
        except (TypeError, ValueError):
            flash("برجاء اختيار مشروع ومورد صحيح.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        project = db.session.get(Project, project_id_value)
        if project is None:
            flash("برجاء اختيار مشروع صحيح.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        supplier = db.session.get(Supplier, supplier_id_value)
        if supplier is None:
            flash("برجاء اختيار مورد صحيح.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        role_name = _get_role()
        if role_name == "engineer" and not project_access_allowed(
            current_user, project_id_value, role_name="engineer"
        ):
            abort(403)
        if role_name == "project_manager":
            pm_project_ids = _project_manager_project_ids() or []
            if project_id_value not in pm_project_ids:
                abort(403)

        try:
            amount = float(amount_str.replace(",", ""))
        except ValueError:
            flash("برجاء إدخال مبلغ صحيح.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        if not math.isfinite(amount) or amount <= 0:
            flash("برجاء إدخال مبلغ صحيح أكبر من صفر.", "danger")
            return redirect(
                url_for("payments.edit_payment", payment_id=payment.id)
            )

        payment.project_id = project.id
        payment.supplier_id = supplier.id
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
    return _redirect_with_return_to("payments.index")


# =========================
#   خطوات الـ Workflow
# =========================

@payments_bp.route("/<int:payment_id>/submit_to_pm", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager", "engineer")
def submit_to_pm(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_PM):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"تم إرسال الدفعة رقم {payment.id} لمدير المشروع",
        message=f"تم تحويل الحالة إلى {payment.human_status}.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("project_manager",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم إرسال الدفعة إلى مدير المشروع للمراجعة.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/pm_approve", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_approve(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_ENG):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"اعتماد مدير المشروع للدفعة رقم {payment.id}",
        message=f"تم تحويل الحالة إلى {payment.human_status}.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("engineering_manager",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم اعتماد الدفعة من مدير المشروع وتم إرسالها للإدارة الهندسية.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/pm_reject", methods=["POST"])
@role_required("admin", "engineering_manager", "project_manager")
def pm_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"رفض مدير المشروع للدفعة رقم {payment.id}",
        message="تم رفض الطلب وإعادته للمراجعة.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=(),
        include_creator=True,
    )
    db.session.commit()

    flash("تم رفض الدفعة من مدير المشروع.", "danger")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/eng_approve", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_approve(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PENDING_FIN):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"اعتماد الإدارة الهندسية للدفعة رقم {payment.id}",
        message=f"تم تحويل الحالة إلى {payment.human_status}.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("finance",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم اعتماد الدفعة من الإدارة الهندسية وتم إرسالها للمالية.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/eng_reject", methods=["POST"])
@role_required("admin", "engineering_manager")
def eng_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"رفض الإدارة الهندسية للدفعة رقم {payment.id}",
        message="تم رفض الطلب وإعادته للمراجعة.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=(),
        include_creator=True,
    )
    db.session.commit()

    flash("تم رفض الدفعة من الإدارة الهندسية.", "danger")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


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
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"اعتماد المالية للدفعة رقم {payment.id}",
        message="أصبحت الدفعة جاهزة للصرف.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("payment_notifier",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم اعتماد الدفعة ماليًا وأصبحت جاهزة للصرف.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/finance_reject", methods=["POST"])
@role_required("admin", "finance")
def finance_reject(payment_id):
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_REJECTED):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

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

    _create_notifications(
        payment,
        title=f"رفض المالية للدفعة رقم {payment.id}",
        message="تم رفض الطلب من المالية.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=(),
        include_creator=True,
    )
    db.session.commit()

    flash("تم رفض الدفعة من المالية.", "danger")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/mark_paid", methods=["POST"])
@role_required("admin", "finance")
def mark_paid(payment_id):
    """
    خطوة تم الصرف:
    - الحالة يجب أن تكون READY_FOR_PAYMENT
    - يُطلب من المالية إدخال finance_amount (المبلغ الفعلي المعتمد)
    - يتم حفظ finance_amount وتغيير الحالة إلى PAID
    """
    payment = _get_payment_or_404(payment_id)

    if not _require_transition(payment, STATUS_PAID):
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    finance_amount_str = (request.form.get("finance_amount") or "").strip()
    if not finance_amount_str:
        flash("برجاء إدخال مبلغ المالية الفعلي قبل تأكيد الصرف.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    finance_amount = _parse_decimal_amount(finance_amount_str)
    if finance_amount is None:
        flash("برجاء إدخال مبلغ مالية فعلي صحيح.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    if finance_amount <= 0:
        flash("برجاء إدخال مبلغ مالية فعلي أكبر من صفر.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    old_status = payment.status
    payment.finance_amount = finance_amount
    payment.status = STATUS_PAID
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="finance",
        action="mark_paid",
        old_status=old_status,
        new_status=payment.status,
    )

    _create_notifications(
        payment,
        title=f"تم صرف الدفعة رقم {payment.id}",
        message=f"تم تسجيل مبلغ الصرف الفعلي {payment.finance_amount:.2f}.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("payment_notifier",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم تسجيل أن الدفعة تم صرفها وحفظ مبلغ المالية الفعلي.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)


@payments_bp.route("/<int:payment_id>/finance-amount", methods=["POST"])
@role_required("finance")
def update_finance_amount(payment_id: int):
    """
    تعديل مبلغ المالية فقط بواسطة دور المالية أثناء المراجعة.
    - يسمح بالعمل في حالات انتظار المالية فقط.
    - يسجل تغييرًا في سجلات الاعتماد.
    """
    payment = _get_payment_or_404(payment_id)

    if payment.status not in FINANCE_AMOUNT_EDITABLE_STATUSES:
        flash("لا يمكن تعديل مبلغ المالية في الحالة الحالية للدفعة.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    if payment.finance_amount is not None:
        flash("تم تثبيت مبلغ المالية الأساسي؛ استخدم تصحيحات الحسابات لأي تعديل.", "warning")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    raw_amount = (request.form.get("finance_amount") or "").strip()
    if not raw_amount:
        flash("برجاء إدخال مبلغ مالية صحيح.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    finance_amount = _parse_decimal_amount(raw_amount)
    if finance_amount is None:
        flash("برجاء إدخال مبلغ مالية صحيح.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    if finance_amount < 0:
        flash("المبلغ المالي يجب أن يكون رقمًا صالحًا أكبر من أو يساوي صفر.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    if finance_amount > Decimal("1000000000"):
        flash("المبلغ المدخل كبير جدًا، يرجى التحقق ثم المحاولة مرة أخرى.", "danger")
        return _redirect_with_return_to("payments.detail", payment_id=payment.id)

    old_amount = payment.finance_amount
    payment.finance_amount = finance_amount
    payment.updated_at = datetime.utcnow()

    _add_approval_log(
        payment,
        step="finance",
        action="update_amount",
        old_status=payment.status,
        new_status=payment.status,
        comment=f"finance_amount: {old_amount} -> {finance_amount}",
    )

    _create_notifications(
        payment,
        title=f"تحديث مبلغ المالية للدفعة رقم {payment.id}",
        message=f"تم تعديل مبلغ المالية من {old_amount} إلى {finance_amount}.",
        url=url_for("payments.detail", payment_id=payment.id),
        roles=("project_manager",),
        include_creator=True,
    )
    db.session.commit()

    flash("تم تحديث مبلغ المالية بنجاح.", "success")
    return _redirect_with_return_to("payments.detail", payment_id=payment.id)
