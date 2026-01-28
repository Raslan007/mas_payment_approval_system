# blueprints/purchase_orders/routes.py

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import logging
from urllib.parse import urljoin, urlparse

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import current_user
from sqlalchemy import false, func, inspect
from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    Project,
    PurchaseOrder,
    PurchaseOrderDecision,
    Supplier,
    PURCHASE_ORDER_STATUS_DRAFT,
    PURCHASE_ORDER_STATUS_SUBMITTED,
    PURCHASE_ORDER_STATUS_PM_APPROVED,
    PURCHASE_ORDER_STATUS_ENG_APPROVED,
    PURCHASE_ORDER_STATUS_FINANCE_APPROVED,
    PURCHASE_ORDER_STATUS_REJECTED,
    get_or_create_supplier_by_name,
    normalize_supplier_name,
)
from permissions import role_required
from project_scopes import get_scoped_project_ids
from . import purchase_orders_bp

logger = logging.getLogger(__name__)

VIEW_ROLES = (
    "procurement",
    "admin",
    "engineering_manager",
    "finance",
    "project_manager",
    "engineer",
)

EDIT_ROLES = ("procurement", "admin")
EDIT_OVERRIDE_ROLES = ("engineering_manager",)

STATUS_META = {
    PURCHASE_ORDER_STATUS_DRAFT: {
        "label": "مسودة",
        "class": "bg-secondary",
    },
    PURCHASE_ORDER_STATUS_SUBMITTED: {
        "label": "مرسل",
        "class": "bg-warning",
    },
    PURCHASE_ORDER_STATUS_PM_APPROVED: {
        "label": "معتمد من مدير المشروع",
        "class": "bg-warning",
    },
    PURCHASE_ORDER_STATUS_ENG_APPROVED: {
        "label": "معتمد من الإدارة الهندسية",
        "class": "bg-warning",
    },
    PURCHASE_ORDER_STATUS_FINANCE_APPROVED: {
        "label": "معتمد من المالية",
        "class": "bg-success",
    },
    PURCHASE_ORDER_STATUS_REJECTED: {
        "label": "مرفوض",
        "class": "bg-danger",
    },
}

ROLE_LABELS = {
    "project_manager": "مدير المشروع",
    "engineering_manager": "مدير الإدارة الهندسية",
    "finance": "المالية",
    "admin": "مسؤول النظام",
}

ALLOWED_STATUSES = {
    PURCHASE_ORDER_STATUS_DRAFT,
    PURCHASE_ORDER_STATUS_SUBMITTED,
    PURCHASE_ORDER_STATUS_PM_APPROVED,
    PURCHASE_ORDER_STATUS_ENG_APPROVED,
    PURCHASE_ORDER_STATUS_FINANCE_APPROVED,
    PURCHASE_ORDER_STATUS_REJECTED,
}

APPROVAL_STAGES = {
    PURCHASE_ORDER_STATUS_SUBMITTED: {
        "required_role": "project_manager",
        "next_status": PURCHASE_ORDER_STATUS_PM_APPROVED,
    },
    PURCHASE_ORDER_STATUS_PM_APPROVED: {
        "required_role": "engineering_manager",
        "next_status": PURCHASE_ORDER_STATUS_ENG_APPROVED,
    },
    PURCHASE_ORDER_STATUS_ENG_APPROVED: {
        "required_role": "finance",
        "next_status": PURCHASE_ORDER_STATUS_FINANCE_APPROVED,
    },
}


def _normalized_role() -> str | None:
    if not current_user.is_authenticated or not current_user.role:
        return None
    role_name = current_user.role.name
    if role_name == "project_engineer":
        return "engineer"
    return role_name


def _scoped_project_ids() -> tuple[str | None, list[int]]:
    normalized_role = _normalized_role()
    scoped_ids = get_scoped_project_ids(current_user, role_name=normalized_role)
    return normalized_role, scoped_ids


def _enforce_project_scope(project_id: int | None, normalized_role: str | None, scoped_ids: list[int]) -> None:
    if project_id is None:
        abort(404)

    if scoped_ids:
        if project_id not in scoped_ids:
            abort(404)
        return

    if normalized_role in {"project_manager", "engineer", "procurement"}:
        abort(404)


def _parse_decimal_amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        parsed = Decimal(raw_value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _sanitize_text(value: str | None, max_length: int) -> str:
    if value is None:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    return trimmed[:max_length]


def _parse_due_date(value: str | None) -> date | None:
    if value is None:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_edit_locked(purchase_order: PurchaseOrder, normalized_role: str | None) -> bool:
    return (
        purchase_order.status == PURCHASE_ORDER_STATUS_ENG_APPROVED
        and normalized_role != "engineering_manager"
    )


def _can_edit_purchase_order(purchase_order: PurchaseOrder, normalized_role: str | None) -> bool:
    return (
        normalized_role in EDIT_ROLES or normalized_role in EDIT_OVERRIDE_ROLES
    ) and not _is_edit_locked(purchase_order, normalized_role)


def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _load_projects(normalized_role: str | None, scoped_ids: list[int]) -> list[Project]:
    query = Project.query.order_by(Project.project_name.asc())
    if scoped_ids:
        query = query.filter(Project.id.in_(scoped_ids))
    elif normalized_role in {"project_manager", "engineer", "procurement"}:
        query = query.filter(false())
    return query.all()


def _status_meta(status: str) -> dict[str, str]:
    return STATUS_META.get(status, {"label": status, "class": "bg-secondary"})


def _approval_stage(status: str) -> dict[str, str] | None:
    return APPROVAL_STAGES.get(status)


def _role_can_act(normalized_role: str | None, required_role: str) -> bool:
    if normalized_role == "admin":
        return True
    if normalized_role == required_role:
        return True
    return normalized_role == "engineering_manager" and required_role == "project_manager"


def _proxy_for_role(normalized_role: str | None, required_role: str | None) -> str | None:
    if normalized_role == "engineering_manager" and required_role == "project_manager":
        return required_role
    return None


def _get_approval_target(status: str, normalized_role: str | None) -> str | None:
    stage = _approval_stage(status)
    if not stage:
        return None
    if not _role_can_act(normalized_role, stage["required_role"]):
        return None
    return stage["next_status"]


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


def _get_return_to(default_endpoint: str = "purchase_orders.index", **default_kwargs) -> str:
    for candidate in (request.values.get("return_to"), request.referrer):
        normalized = _normalize_return_to(candidate)
        if normalized and _is_safe_return_to(normalized):
            return normalized

    return url_for(default_endpoint, **default_kwargs)


@lru_cache(maxsize=1)
def _purchase_orders_column_names() -> set[str]:
    inspector = inspect(db.engine)
    if not inspector.has_table("purchase_orders"):
        return set()
    return {column["name"] for column in inspector.get_columns("purchase_orders")}


def _purchase_orders_has_deleted_at() -> bool:
    return "deleted_at" in _purchase_orders_column_names()


def _purchase_orders_has_soft_delete_fields() -> bool:
    column_names = _purchase_orders_column_names()
    return "deleted_at" in column_names and "deleted_by_id" in column_names


def _active_purchase_orders_query():
    if _purchase_orders_has_deleted_at():
        return PurchaseOrder.query.filter(PurchaseOrder.deleted_at.is_(None))
    return PurchaseOrder.query


@purchase_orders_bp.route("/")
@role_required(*VIEW_ROLES)
def index():
    normalized_role, scoped_ids = _scoped_project_ids()

    filters = {
        "project_id": request.args.get("project_id", type=int),
        "status": (request.args.get("status") or "").strip(),
        "bo_number": (request.args.get("bo_number") or "").strip(),
        "supplier_name": (request.args.get("supplier_name") or "").strip(),
    }

    query = _active_purchase_orders_query().options(
        selectinload(PurchaseOrder.project),
        selectinload(PurchaseOrder.created_by),
    )

    if normalized_role in {"project_manager", "engineer", "procurement"}:
        if scoped_ids:
            query = query.filter(PurchaseOrder.project_id.in_(scoped_ids))
        else:
            query = query.filter(false())
    elif scoped_ids:
        query = query.filter(PurchaseOrder.project_id.in_(scoped_ids))

    if filters["project_id"]:
        query = query.filter(PurchaseOrder.project_id == filters["project_id"])
    if filters["status"] in ALLOWED_STATUSES:
        query = query.filter(PurchaseOrder.status == filters["status"])
    if filters["bo_number"]:
        query = query.filter(PurchaseOrder.bo_number.ilike(f"%{filters['bo_number']}%"))
    if filters["supplier_name"]:
        query = query.filter(PurchaseOrder.supplier_name.ilike(f"%{filters['supplier_name']}%"))

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

    pagination = (
        query.order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    projects = _load_projects(normalized_role, scoped_ids)
    can_create = normalized_role in EDIT_ROLES
    can_edit_override = normalized_role in EDIT_OVERRIDE_ROLES
    can_delete = normalized_role == "admin"
    pagination_params = {
        key: value
        for key, value in request.args.items()
        if key not in {"page", "per_page"} and value
    }

    return render_template(
        "purchase_orders/index.html",
        purchase_orders=pagination.items,
        pagination=pagination,
        filters=filters,
        projects=projects,
        status_meta=STATUS_META,
        can_create=can_create,
        can_edit_override=can_edit_override,
        can_delete=can_delete,
        pagination_params=pagination_params,
        page=page,
        per_page=per_page,
        page_title="أوامر الشراء",
    )


@purchase_orders_bp.route("/new")
@role_required(*EDIT_ROLES)
def new():
    normalized_role, scoped_ids = _scoped_project_ids()
    projects = _load_projects(normalized_role, scoped_ids)
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    back_url = _get_return_to()
    prefill_project_id = request.args.get("project_id", type=int)
    prefill_description = _sanitize_text(request.args.get("description"), 2000)
    prefill_reference = _sanitize_text(request.args.get("reference_po_number"), 50)
    if prefill_project_id:
        _enforce_project_scope(prefill_project_id, normalized_role, scoped_ids)
        if not any(project.id == prefill_project_id for project in projects):
            prefill_project_id = None

    return render_template(
        "purchase_orders/form.html",
        purchase_order=None,
        projects=projects,
        suppliers=suppliers,
        status_meta=_status_meta(PURCHASE_ORDER_STATUS_DRAFT),
        action_url=url_for("purchase_orders.create"),
        submit_label="إضافة أمر شراء",
        back_url=back_url,
        prefill_project_id=prefill_project_id,
        prefill_description=prefill_description,
        prefill_reference_po_number=prefill_reference,
        page_title="إضافة أمر شراء",
    )


@purchase_orders_bp.route("/", methods=["POST"])
@role_required(*EDIT_ROLES)
def create():
    normalized_role, scoped_ids = _scoped_project_ids()

    bo_number = (request.form.get("bo_number") or "").strip()
    description = _sanitize_text(request.form.get("description"), 2000) or None
    reference_po_number = _sanitize_text(request.form.get("reference_po_number"), 50) or None
    supplier_id = request.form.get("supplier_id", type=int)
    supplier_name = normalize_supplier_name(request.form.get("supplier_name") or "")
    project_id = request.form.get("project_id", type=int)
    total_amount_raw = request.form.get("total_amount")
    advance_amount_raw = request.form.get("advance_amount")
    due_date_raw = request.form.get("due_date")
    total_amount = _parse_decimal_amount(total_amount_raw)
    advance_amount = _parse_decimal_amount(advance_amount_raw)
    due_date = _parse_due_date(due_date_raw)
    if advance_amount_raw is None or not advance_amount_raw.strip():
        advance_amount = Decimal("0.00")
    if total_amount is not None:
        total_amount = _quantize_amount(total_amount)
    if advance_amount is not None:
        advance_amount = _quantize_amount(advance_amount)

    errors: list[str] = []
    if not bo_number:
        errors.append("يرجى إدخال رقم BO.")
    if not supplier_id and not supplier_name:
        errors.append("يرجى اختيار المورد أو إدخال اسم مورد جديد.")
    if not project_id:
        errors.append("يرجى اختيار المشروع.")
    if total_amount is None:
        errors.append("يرجى إدخال إجمالي المبلغ بشكل صحيح.")
    if advance_amount_raw and advance_amount is None:
        errors.append("يرجى إدخال مبلغ الدفعة المقدمة بشكل صحيح.")
    if due_date_raw and due_date is None:
        errors.append("يرجى إدخال تاريخ الاستحقاق بشكل صحيح.")

    if total_amount is not None and total_amount < 0:
        errors.append("إجمالي المبلغ يجب ألا يكون سالباً.")
    if advance_amount is not None and advance_amount < 0:
        errors.append("الدفعة المقدمة يجب ألا تكون سالبة.")
    if total_amount is not None and advance_amount is not None and advance_amount > total_amount:
        errors.append("الدفعة المقدمة يجب ألا تتجاوز إجمالي المبلغ.")

    if project_id:
        _enforce_project_scope(project_id, normalized_role, scoped_ids)

    supplier = None
    if supplier_id:
        supplier = db.session.get(Supplier, supplier_id)
        if supplier is None:
            errors.append("المورد المحدد غير موجود.")
    elif supplier_name:
        supplier = get_or_create_supplier_by_name(supplier_name)
        if getattr(supplier, "was_created", False):
            logger.info(
                "PO create created supplier_id=%s name='%s'.",
                supplier.id,
                supplier.name,
            )
        else:
            logger.info(
                "PO create reused supplier_id=%s name='%s'.",
                supplier.id,
                supplier.name,
            )

    existing = None
    if bo_number:
        existing = PurchaseOrder.query.filter(
            func.lower(PurchaseOrder.bo_number) == bo_number.lower()
        ).first()
    if existing:
        errors.append("رقم BO مستخدم مسبقاً.")

    if errors:
        for message in errors:
            flash(message, "danger")
        query_params: dict[str, str | int] = {}
        if project_id:
            query_params["project_id"] = project_id
        if description:
            query_params["description"] = description
        if reference_po_number:
            query_params["reference_po_number"] = reference_po_number
        return redirect(url_for("purchase_orders.new", **query_params))

    remaining_amount = (total_amount or Decimal("0.00")) - (advance_amount or Decimal("0.00"))
    remaining_amount = _quantize_amount(remaining_amount)

    purchase_order = PurchaseOrder(
        bo_number=bo_number,
        description=description,
        reference_po_number=reference_po_number,
        project_id=project_id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        total_amount=total_amount,
        advance_amount=advance_amount,
        remaining_amount=remaining_amount,
        due_date=due_date,
        status=PURCHASE_ORDER_STATUS_DRAFT,
        created_by_id=current_user.id,
    )

    db.session.add(purchase_order)
    db.session.commit()
    flash("تم إنشاء أمر الشراء بنجاح.", "success")
    if reference_po_number and supplier is not None:
        source_po = PurchaseOrder.query.filter(
            func.lower(PurchaseOrder.bo_number) == reference_po_number.lower()
        ).first()
        if source_po and source_po.supplier_id == supplier.id:
            flash("تنبيه: تم اختيار نفس المورد الموجود في أمر الشراء المرجعي.", "warning")
    if due_date is None:
        flash("يفضل إضافة تاريخ الاستحقاق لتسهيل المتابعة.", "warning")
    return redirect(url_for("purchase_orders.detail", id=purchase_order.id))


@purchase_orders_bp.route("/<int:id>")
@role_required(*VIEW_ROLES)
def detail(id: int):
    purchase_order = _active_purchase_orders_query().options(
        selectinload(PurchaseOrder.project),
        selectinload(PurchaseOrder.created_by),
        selectinload(PurchaseOrder.decisions).selectinload(PurchaseOrderDecision.decided_by),
    ).filter(PurchaseOrder.id == id).first_or_404()

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    stage = _approval_stage(purchase_order.status)
    approval_required_role = stage["required_role"] if stage else None
    approval_target = _get_approval_target(purchase_order.status, normalized_role)
    can_approve = approval_target is not None
    can_reject = approval_target is not None
    can_edit = _can_edit_purchase_order(purchase_order, normalized_role)
    can_submit = normalized_role in EDIT_ROLES and purchase_order.status == PURCHASE_ORDER_STATUS_DRAFT
    can_clone = normalized_role in EDIT_ROLES
    is_proxy_action = (
        normalized_role == "engineering_manager"
        and approval_required_role == "project_manager"
    )
    back_url = _get_return_to()

    return render_template(
        "purchase_orders/detail.html",
        purchase_order=purchase_order,
        status_meta=_status_meta(purchase_order.status),
        status_meta_map=STATUS_META,
        role_labels=ROLE_LABELS,
        approval_required_role=approval_required_role,
        is_proxy_action=is_proxy_action,
        can_edit=can_edit,
        can_submit=can_submit,
        can_clone=can_clone,
        can_approve=can_approve,
        can_reject=can_reject,
        back_url=back_url,
        page_title=f"أمر شراء رقم {purchase_order.bo_number}",
    )


@purchase_orders_bp.route("/<int:id>/clone_for_other_vendor", methods=["POST"])
@role_required(*EDIT_ROLES)
def clone_for_other_vendor(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()
    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)
    reference_po_number = _sanitize_text(purchase_order.bo_number, 50)
    description = _sanitize_text(purchase_order.description, 2000)
    query_params: dict[str, str | int] = {
        "project_id": purchase_order.project_id,
    }
    if description:
        query_params["description"] = description
    if reference_po_number:
        query_params["reference_po_number"] = reference_po_number
    return redirect(url_for("purchase_orders.new", **query_params))


@purchase_orders_bp.route("/<int:id>/edit")
@role_required(*EDIT_ROLES, *EDIT_OVERRIDE_ROLES)
def edit(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()
    normalized_role, scoped_ids = _scoped_project_ids()
    if _is_edit_locked(purchase_order, normalized_role):
        flash("لا يمكن تعديل أمر الشراء بعد الاعتماد الهندسي.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)
    projects = _load_projects(normalized_role, scoped_ids)
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    back_url = _get_return_to()

    return render_template(
        "purchase_orders/form.html",
        purchase_order=purchase_order,
        projects=projects,
        suppliers=suppliers,
        status_meta=_status_meta(purchase_order.status),
        action_url=url_for("purchase_orders.update", id=id),
        submit_label="حفظ التعديلات",
        back_url=back_url,
        page_title=f"تعديل أمر شراء {purchase_order.bo_number}",
    )


@purchase_orders_bp.route("/<int:id>/update", methods=["POST"])
@role_required(*EDIT_ROLES, *EDIT_OVERRIDE_ROLES)
def update(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()
    normalized_role, scoped_ids = _scoped_project_ids()
    if _is_edit_locked(purchase_order, normalized_role):
        flash("لا يمكن تعديل أمر الشراء بعد الاعتماد الهندسي.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    bo_number = (request.form.get("bo_number") or "").strip()
    description = _sanitize_text(request.form.get("description"), 2000) or None
    reference_po_number = _sanitize_text(request.form.get("reference_po_number"), 50) or None
    supplier_id = request.form.get("supplier_id", type=int)
    supplier_name = normalize_supplier_name(request.form.get("supplier_name") or "")
    project_id = request.form.get("project_id", type=int)
    total_amount_raw = request.form.get("total_amount")
    advance_amount_raw = request.form.get("advance_amount")
    due_date_raw = request.form.get("due_date")
    total_amount = _parse_decimal_amount(total_amount_raw)
    advance_amount = _parse_decimal_amount(advance_amount_raw)
    due_date = _parse_due_date(due_date_raw)
    if advance_amount_raw is None or not advance_amount_raw.strip():
        advance_amount = Decimal("0.00")
    if total_amount is not None:
        total_amount = _quantize_amount(total_amount)
    if advance_amount is not None:
        advance_amount = _quantize_amount(advance_amount)

    errors: list[str] = []
    if not bo_number:
        errors.append("يرجى إدخال رقم BO.")
    if not supplier_id and not supplier_name:
        errors.append("يرجى اختيار المورد أو إدخال اسم مورد جديد.")
    if not project_id:
        errors.append("يرجى اختيار المشروع.")
    if total_amount is None:
        errors.append("يرجى إدخال إجمالي المبلغ بشكل صحيح.")
    if advance_amount_raw and advance_amount is None:
        errors.append("يرجى إدخال مبلغ الدفعة المقدمة بشكل صحيح.")
    if due_date_raw and due_date is None:
        errors.append("يرجى إدخال تاريخ الاستحقاق بشكل صحيح.")

    if total_amount is not None and total_amount < 0:
        errors.append("إجمالي المبلغ يجب ألا يكون سالباً.")
    if advance_amount is not None and advance_amount < 0:
        errors.append("الدفعة المقدمة يجب ألا تكون سالبة.")
    if total_amount is not None and advance_amount is not None and advance_amount > total_amount:
        errors.append("الدفعة المقدمة يجب ألا تتجاوز إجمالي المبلغ.")

    if project_id:
        _enforce_project_scope(project_id, normalized_role, scoped_ids)

    supplier = None
    if supplier_id:
        supplier = db.session.get(Supplier, supplier_id)
        if supplier is None:
            errors.append("المورد المحدد غير موجود.")
    elif supplier_name:
        supplier = get_or_create_supplier_by_name(supplier_name)
        if getattr(supplier, "was_created", False):
            logger.info(
                "PO update created supplier_id=%s name='%s'.",
                supplier.id,
                supplier.name,
            )
        else:
            logger.info(
                "PO update reused supplier_id=%s name='%s'.",
                supplier.id,
                supplier.name,
            )

    if bo_number:
        existing = PurchaseOrder.query.filter(
            func.lower(PurchaseOrder.bo_number) == bo_number.lower(),
            PurchaseOrder.id != purchase_order.id,
        ).first()
        if existing:
            errors.append("رقم BO مستخدم مسبقاً.")

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("purchase_orders.edit", id=id))

    purchase_order.bo_number = bo_number
    purchase_order.description = description
    purchase_order.reference_po_number = reference_po_number
    purchase_order.project_id = project_id
    purchase_order.supplier_id = supplier.id
    purchase_order.supplier_name = supplier.name
    purchase_order.total_amount = total_amount
    purchase_order.advance_amount = advance_amount
    purchase_order.due_date = due_date
    purchase_order.remaining_amount = _quantize_amount(
        (total_amount or Decimal("0.00")) - (advance_amount or Decimal("0.00"))
    )

    db.session.commit()
    flash("تم تحديث أمر الشراء بنجاح.", "success")
    if due_date is None:
        flash("يفضل إضافة تاريخ الاستحقاق لتسهيل المتابعة.", "warning")
    return redirect(url_for("purchase_orders.detail", id=id))


@purchase_orders_bp.route("/<int:id>/submit", methods=["POST"])
@role_required(*EDIT_ROLES)
def submit(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    if purchase_order.status != PURCHASE_ORDER_STATUS_DRAFT:
        flash("يمكن إرسال المسودات فقط.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))

    decision = PurchaseOrderDecision(
        purchase_order_id=purchase_order.id,
        action="submit",
        from_status=purchase_order.status,
        to_status=PURCHASE_ORDER_STATUS_SUBMITTED,
        comment=None,
        decided_by_id=current_user.id,
    )
    db.session.add(decision)
    purchase_order.status = PURCHASE_ORDER_STATUS_SUBMITTED
    db.session.commit()

    flash("تم إرسال أمر الشراء بنجاح.", "success")
    return redirect(url_for("purchase_orders.detail", id=id))


@purchase_orders_bp.route("/<int:id>/approve", methods=["POST"])
@role_required("project_manager", "engineering_manager", "finance", "admin")
def approve(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    stage = _approval_stage(purchase_order.status)
    required_role = stage["required_role"] if stage else None
    next_status = _get_approval_target(purchase_order.status, normalized_role)
    if next_status is None:
        flash("لا يمكن اعتماد أمر الشراء في هذه المرحلة.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))

    comment = (request.form.get("comment") or "").strip() or None
    decision = PurchaseOrderDecision(
        purchase_order_id=purchase_order.id,
        action="approve",
        from_status=purchase_order.status,
        to_status=next_status,
        comment=comment,
        proxy_for_role=_proxy_for_role(normalized_role, required_role),
        decided_by_id=current_user.id,
    )
    db.session.add(decision)
    purchase_order.status = next_status
    db.session.commit()

    flash("تم اعتماد أمر الشراء بنجاح.", "success")
    return redirect(url_for("purchase_orders.detail", id=id))


@purchase_orders_bp.route("/<int:id>/reject", methods=["POST"])
@role_required("project_manager", "engineering_manager", "finance", "admin")
def reject(id: int):
    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    stage = _approval_stage(purchase_order.status)
    required_role = stage["required_role"] if stage else None
    approval_target = _get_approval_target(purchase_order.status, normalized_role)
    if approval_target is None:
        flash("لا يمكن رفض أمر الشراء في هذه المرحلة.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))

    comment = (request.form.get("comment") or "").strip() or None
    decision = PurchaseOrderDecision(
        purchase_order_id=purchase_order.id,
        action="reject",
        from_status=purchase_order.status,
        to_status=PURCHASE_ORDER_STATUS_REJECTED,
        comment=comment,
        proxy_for_role=_proxy_for_role(normalized_role, required_role),
        decided_by_id=current_user.id,
    )
    db.session.add(decision)
    purchase_order.status = PURCHASE_ORDER_STATUS_REJECTED
    db.session.commit()

    flash("تم رفض أمر الشراء.", "success")
    return redirect(url_for("purchase_orders.detail", id=id))


@purchase_orders_bp.route("/<int:id>/delete", methods=["POST"])
@role_required("admin")
def delete(id: int):
    if not _purchase_orders_has_soft_delete_fields():
        flash("لا يمكن حذف أمر الشراء حالياً. يرجى إعادة المحاولة لاحقاً.", "danger")
        logger.warning(
            "PO soft delete attempted before schema patch; id=%s",
            id,
        )
        return redirect(url_for("purchase_orders.detail", id=id))

    purchase_order = _active_purchase_orders_query().filter(PurchaseOrder.id == id).first_or_404()
    purchase_order.soft_delete(current_user)
    db.session.commit()
    logger.info(
        "PO soft delete id=%s bo_number=%s deleted_by_id=%s",
        purchase_order.id,
        purchase_order.bo_number,
        current_user.id if current_user.is_authenticated else None,
    )
    flash("تم حذف أمر الشراء بنجاح.", "success")
    return redirect(url_for("purchase_orders.index"))
