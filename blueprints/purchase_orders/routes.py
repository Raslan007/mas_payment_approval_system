# blueprints/purchase_orders/routes.py

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import current_user
from sqlalchemy import false, func
from sqlalchemy.orm import selectinload

from extensions import db
from models import (
    Project,
    PurchaseOrder,
    PurchaseOrderDecision,
    PURCHASE_ORDER_STATUS_DRAFT,
    PURCHASE_ORDER_STATUS_SUBMITTED,
    PURCHASE_ORDER_STATUS_PM_APPROVED,
    PURCHASE_ORDER_STATUS_ENG_APPROVED,
    PURCHASE_ORDER_STATUS_FINANCE_APPROVED,
    PURCHASE_ORDER_STATUS_REJECTED,
)
from permissions import role_required
from project_scopes import get_scoped_project_ids
from . import purchase_orders_bp

VIEW_ROLES = (
    "procurement",
    "admin",
    "engineering_manager",
    "finance",
    "project_manager",
    "engineer",
)

EDIT_ROLES = ("procurement", "admin")

STATUS_META = {
    PURCHASE_ORDER_STATUS_DRAFT: {
        "label": "مسودة",
        "class": "badge-status status-draft",
    },
    PURCHASE_ORDER_STATUS_SUBMITTED: {
        "label": "مرسل",
        "class": "badge-status badge-status--pending",
    },
    PURCHASE_ORDER_STATUS_PM_APPROVED: {
        "label": "معتمد من مدير المشروع",
        "class": "badge-status badge-status--pending",
    },
    PURCHASE_ORDER_STATUS_ENG_APPROVED: {
        "label": "معتمد من الإدارة الهندسية",
        "class": "badge-status badge-status--pending",
    },
    PURCHASE_ORDER_STATUS_FINANCE_APPROVED: {
        "label": "معتمد من المالية",
        "class": "badge-status badge-status--success",
    },
    PURCHASE_ORDER_STATUS_REJECTED: {
        "label": "مرفوض",
        "class": "badge-status badge-status--danger",
    },
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


def _load_projects(normalized_role: str | None, scoped_ids: list[int]) -> list[Project]:
    query = Project.query.order_by(Project.project_name.asc())
    if scoped_ids:
        query = query.filter(Project.id.in_(scoped_ids))
    elif normalized_role in {"project_manager", "engineer", "procurement"}:
        query = query.filter(false())
    return query.all()


def _status_meta(status: str) -> dict[str, str]:
    return STATUS_META.get(status, {"label": status, "class": "badge bg-secondary"})


def _approval_stage(status: str) -> dict[str, str] | None:
    return APPROVAL_STAGES.get(status)


def _role_can_act(normalized_role: str | None, required_role: str) -> bool:
    return normalized_role == required_role or normalized_role == "admin"


def _get_approval_target(status: str, normalized_role: str | None) -> str | None:
    stage = _approval_stage(status)
    if not stage:
        return None
    if not _role_can_act(normalized_role, stage["required_role"]):
        return None
    return stage["next_status"]


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

    query = PurchaseOrder.query.options(
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

    return render_template(
        "purchase_orders/form.html",
        purchase_order=None,
        projects=projects,
        status_meta=_status_meta(PURCHASE_ORDER_STATUS_DRAFT),
        action_url=url_for("purchase_orders.create"),
        submit_label="إضافة أمر شراء",
        page_title="إضافة أمر شراء",
    )


@purchase_orders_bp.route("/", methods=["POST"])
@role_required(*EDIT_ROLES)
def create():
    normalized_role, scoped_ids = _scoped_project_ids()

    bo_number = (request.form.get("bo_number") or "").strip()
    supplier_name = (request.form.get("supplier_name") or "").strip()
    project_id = request.form.get("project_id", type=int)
    total_amount_raw = request.form.get("total_amount")
    advance_amount_raw = request.form.get("advance_amount")
    total_amount = _parse_decimal_amount(total_amount_raw)
    advance_amount = _parse_decimal_amount(advance_amount_raw)
    if advance_amount_raw is None or not advance_amount_raw.strip():
        advance_amount = Decimal("0.00")

    errors: list[str] = []
    if not bo_number:
        errors.append("يرجى إدخال رقم BO.")
    if not supplier_name:
        errors.append("يرجى إدخال اسم المورد.")
    if not project_id:
        errors.append("يرجى اختيار المشروع.")
    if total_amount is None:
        errors.append("يرجى إدخال إجمالي المبلغ بشكل صحيح.")
    if advance_amount_raw and advance_amount is None:
        errors.append("يرجى إدخال مبلغ الدفعة المقدمة بشكل صحيح.")

    if total_amount is not None and total_amount < 0:
        errors.append("إجمالي المبلغ يجب ألا يكون سالباً.")
    if advance_amount is not None and advance_amount < 0:
        errors.append("الدفعة المقدمة يجب ألا تكون سالبة.")
    if total_amount is not None and advance_amount is not None and advance_amount > total_amount:
        errors.append("الدفعة المقدمة يجب ألا تتجاوز إجمالي المبلغ.")

    if project_id:
        _enforce_project_scope(project_id, normalized_role, scoped_ids)

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
        return redirect(url_for("purchase_orders.new"))

    remaining_amount = (total_amount or Decimal("0.00")) - (advance_amount or Decimal("0.00"))

    purchase_order = PurchaseOrder(
        bo_number=bo_number,
        project_id=project_id,
        supplier_name=supplier_name,
        total_amount=total_amount,
        advance_amount=advance_amount,
        remaining_amount=remaining_amount,
        status=PURCHASE_ORDER_STATUS_DRAFT,
        created_by_id=current_user.id,
    )

    db.session.add(purchase_order)
    db.session.commit()
    flash("تم إنشاء أمر الشراء بنجاح.", "success")
    return redirect(url_for("purchase_orders.detail", id=purchase_order.id))


@purchase_orders_bp.route("/<int:id>")
@role_required(*VIEW_ROLES)
def detail(id: int):
    purchase_order = PurchaseOrder.query.options(
        selectinload(PurchaseOrder.project),
        selectinload(PurchaseOrder.created_by),
        selectinload(PurchaseOrder.decisions).selectinload(PurchaseOrderDecision.decided_by),
    ).get_or_404(id)

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    approval_target = _get_approval_target(purchase_order.status, normalized_role)
    can_approve = approval_target is not None
    can_reject = approval_target is not None
    can_edit = normalized_role in EDIT_ROLES and purchase_order.status == PURCHASE_ORDER_STATUS_DRAFT

    return render_template(
        "purchase_orders/detail.html",
        purchase_order=purchase_order,
        status_meta=_status_meta(purchase_order.status),
        status_meta_map=STATUS_META,
        can_edit=can_edit,
        can_approve=can_approve,
        can_reject=can_reject,
        page_title=f"أمر شراء رقم {purchase_order.bo_number}",
    )


@purchase_orders_bp.route("/<int:id>/edit")
@role_required(*EDIT_ROLES)
def edit(id: int):
    purchase_order = PurchaseOrder.query.get_or_404(id)
    if purchase_order.status != PURCHASE_ORDER_STATUS_DRAFT:
        flash("لا يمكن تعديل أمر شراء بعد الإرسال.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)
    projects = _load_projects(normalized_role, scoped_ids)

    return render_template(
        "purchase_orders/form.html",
        purchase_order=purchase_order,
        projects=projects,
        status_meta=_status_meta(purchase_order.status),
        action_url=url_for("purchase_orders.update", id=id),
        submit_label="حفظ التعديلات",
        page_title=f"تعديل أمر شراء {purchase_order.bo_number}",
    )


@purchase_orders_bp.route("/<int:id>/update", methods=["POST"])
@role_required(*EDIT_ROLES)
def update(id: int):
    purchase_order = PurchaseOrder.query.get_or_404(id)
    if purchase_order.status != PURCHASE_ORDER_STATUS_DRAFT:
        flash("لا يمكن تعديل أمر شراء بعد الإرسال.", "warning")
        return redirect(url_for("purchase_orders.detail", id=id))

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

    bo_number = (request.form.get("bo_number") or "").strip()
    supplier_name = (request.form.get("supplier_name") or "").strip()
    project_id = request.form.get("project_id", type=int)
    total_amount_raw = request.form.get("total_amount")
    advance_amount_raw = request.form.get("advance_amount")
    total_amount = _parse_decimal_amount(total_amount_raw)
    advance_amount = _parse_decimal_amount(advance_amount_raw)
    if advance_amount_raw is None or not advance_amount_raw.strip():
        advance_amount = Decimal("0.00")

    errors: list[str] = []
    if not bo_number:
        errors.append("يرجى إدخال رقم BO.")
    if not supplier_name:
        errors.append("يرجى إدخال اسم المورد.")
    if not project_id:
        errors.append("يرجى اختيار المشروع.")
    if total_amount is None:
        errors.append("يرجى إدخال إجمالي المبلغ بشكل صحيح.")
    if advance_amount_raw and advance_amount is None:
        errors.append("يرجى إدخال مبلغ الدفعة المقدمة بشكل صحيح.")

    if total_amount is not None and total_amount < 0:
        errors.append("إجمالي المبلغ يجب ألا يكون سالباً.")
    if advance_amount is not None and advance_amount < 0:
        errors.append("الدفعة المقدمة يجب ألا تكون سالبة.")
    if total_amount is not None and advance_amount is not None and advance_amount > total_amount:
        errors.append("الدفعة المقدمة يجب ألا تتجاوز إجمالي المبلغ.")

    if project_id:
        _enforce_project_scope(project_id, normalized_role, scoped_ids)

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
    purchase_order.project_id = project_id
    purchase_order.supplier_name = supplier_name
    purchase_order.total_amount = total_amount
    purchase_order.advance_amount = advance_amount
    purchase_order.remaining_amount = (total_amount or Decimal("0.00")) - (advance_amount or Decimal("0.00"))

    db.session.commit()
    flash("تم تحديث أمر الشراء بنجاح.", "success")
    return redirect(url_for("purchase_orders.detail", id=id))


@purchase_orders_bp.route("/<int:id>/submit", methods=["POST"])
@role_required(*EDIT_ROLES)
def submit(id: int):
    purchase_order = PurchaseOrder.query.get_or_404(id)

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
    purchase_order = PurchaseOrder.query.get_or_404(id)

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

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
    purchase_order = PurchaseOrder.query.get_or_404(id)

    normalized_role, scoped_ids = _scoped_project_ids()
    _enforce_project_scope(purchase_order.project_id, normalized_role, scoped_ids)

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
        decided_by_id=current_user.id,
    )
    db.session.add(decision)
    purchase_order.status = PURCHASE_ORDER_STATUS_REJECTED
    db.session.commit()

    flash("تم رفض أمر الشراء.", "success")
    return redirect(url_for("purchase_orders.detail", id=id))
