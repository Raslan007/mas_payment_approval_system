# blueprints/finance/routes.py

import csv
import io
from datetime import timedelta
from decimal import Decimal

from flask import Response, render_template, request
from sqlalchemy import case, func

from permissions import role_required
from extensions import db
from models import (
    PaymentFinanceAdjustment,
    PaymentRequest,
    Project,
    Supplier,
    SupplierLedgerEntry,
)
from blueprints.finance import finance_bp
from blueprints.payments import routes as payment_routes

STATUS_PENDING_FIN = payment_routes.STATUS_PENDING_FIN
STATUS_READY_FOR_PAYMENT = payment_routes.STATUS_READY_FOR_PAYMENT
STATUS_PAID = payment_routes.STATUS_PAID

FINANCE_ALLOWED_STATUSES = (
    STATUS_PENDING_FIN,
    STATUS_READY_FOR_PAYMENT,
    STATUS_PAID,
)

LEGACY_LIABILITY_ROLES = (
    "admin",
    "engineering_manager",
    "procurement",
    "accounts",
    "chairman",
    "finance",
)


def _safe_float_arg(name: str) -> float | None:
    raw_value = (request.args.get(name) or "").strip()
    if not raw_value:
        return None
    try:
        return float(raw_value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _finance_workbench_ordering(status_filter: str) -> tuple:
    if status_filter == STATUS_PAID:
        return (PaymentRequest.created_at.desc(), PaymentRequest.id.desc())
    return (PaymentRequest.created_at.asc(), PaymentRequest.id.asc())


def _finance_workbench_query():
    adjustments_subq = (
        db.session.query(
            PaymentFinanceAdjustment.payment_id.label("payment_id"),
            func.coalesce(func.sum(PaymentFinanceAdjustment.delta_amount), 0).label(
                "adjustments_total"
            ),
        )
        .filter(PaymentFinanceAdjustment.is_void.is_(False))
        .group_by(PaymentFinanceAdjustment.payment_id)
        .subquery()
    )
    effective_amount_expr = (
        func.coalesce(PaymentRequest.finance_amount, 0)
        + func.coalesce(adjustments_subq.c.adjustments_total, 0)
    )
    adjustments_total_expr = func.coalesce(
        adjustments_subq.c.adjustments_total, 0
    ).label("finance_adjustments_total")

    q = (
        PaymentRequest.query.options(*payment_routes.PAYMENT_RELATION_OPTIONS)
        .outerjoin(
            adjustments_subq,
            PaymentRequest.id == adjustments_subq.c.payment_id,
        )
        .filter(PaymentRequest.status.in_(FINANCE_ALLOWED_STATUSES))
    )

    projects = Project.query.order_by(Project.project_name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    _, request_types, _ = payment_routes._get_filter_lists()
    allowed_request_types = set(filter(None, request_types)) | {"مقاول", "مشتريات", "عهدة"}

    filters = {
        "status": "",
        "project_id": "",
        "supplier_id": "",
        "request_type": "",
        "date_from": "",
        "date_to": "",
        "amount_min": "",
        "amount_max": "",
        "finance_amount_min": "",
        "finance_amount_max": "",
    }

    status_filter = (request.args.get("tab") or request.args.get("status") or "").strip()
    if status_filter in FINANCE_ALLOWED_STATUSES:
        filters["status"] = status_filter
        q = q.filter(PaymentRequest.status == status_filter)
    else:
        filters["status"] = STATUS_PENDING_FIN
        q = q.filter(PaymentRequest.status == STATUS_PENDING_FIN)
        status_filter = STATUS_PENDING_FIN

    project_id = payment_routes._safe_int_arg("project_id", None, min_value=1)
    if project_id:
        filters["project_id"] = str(project_id)
        q = q.filter(PaymentRequest.project_id == project_id)

    supplier_id = payment_routes._safe_int_arg("supplier_id", None, min_value=1)
    if supplier_id:
        filters["supplier_id"] = str(supplier_id)
        q = q.filter(PaymentRequest.supplier_id == supplier_id)

    raw_request_type = (request.args.get("request_type") or "").strip()
    if raw_request_type and raw_request_type in allowed_request_types:
        filters["request_type"] = raw_request_type
        q = q.filter(PaymentRequest.request_type == raw_request_type)

    date_from_dt = payment_routes._safe_date_arg("date_from")
    if date_from_dt:
        filters["date_from"] = date_from_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at >= date_from_dt)

    date_to_dt = payment_routes._safe_date_arg("date_to")
    if date_to_dt:
        filters["date_to"] = date_to_dt.strftime("%Y-%m-%d")
        q = q.filter(PaymentRequest.created_at < date_to_dt + timedelta(days=1))

    amount_min = _safe_float_arg("amount_min")
    if amount_min is not None:
        filters["amount_min"] = (request.args.get("amount_min") or "").strip()
        q = q.filter(PaymentRequest.amount >= amount_min)

    amount_max = _safe_float_arg("amount_max")
    if amount_max is not None:
        filters["amount_max"] = (request.args.get("amount_max") or "").strip()
        q = q.filter(PaymentRequest.amount <= amount_max)

    finance_amount_min = _safe_float_arg("finance_amount_min")
    if finance_amount_min is not None:
        filters["finance_amount_min"] = (request.args.get("finance_amount_min") or "").strip()
        q = q.filter(effective_amount_expr >= finance_amount_min)

    finance_amount_max = _safe_float_arg("finance_amount_max")
    if finance_amount_max is not None:
        filters["finance_amount_max"] = (request.args.get("finance_amount_max") or "").strip()
        q = q.filter(effective_amount_expr <= finance_amount_max)

    q = q.add_columns(
        adjustments_total_expr,
        effective_amount_expr.label("finance_effective_amount"),
    )

    return (
        q,
        filters,
        projects,
        suppliers,
        request_types,
        status_filter,
        _finance_workbench_ordering(status_filter),
        effective_amount_expr,
    )


def _paginate_finance_query(q, order_clause: tuple):
    page = payment_routes._safe_int_arg("page", 1, min_value=1) or 1
    per_page = payment_routes._safe_int_arg("per_page", 20, min_value=1, max_value=100) or 20

    total_count = (
        q.order_by(None)
        .with_entities(func.count(PaymentRequest.id))
        .scalar()
        or 0
    )

    ordered_q = q.order_by(*order_clause)
    pagination = ordered_q.paginate(
        page=page, per_page=per_page, error_out=False, count=False
    )
    pagination.total = total_count
    return pagination, page, per_page


def _finance_workbench_kpis(q, effective_amount_expr) -> dict:
    base_value = func.coalesce(effective_amount_expr, PaymentRequest.amount, 0)
    aggregates = (
        q.order_by(None)
        .with_entities(
            func.count(PaymentRequest.id),
            func.coalesce(func.sum(PaymentRequest.amount), 0.0),
            func.coalesce(func.sum(effective_amount_expr), 0.0),
            func.coalesce(
                func.sum(
                    case(
                        (PaymentRequest.status == STATUS_READY_FOR_PAYMENT, base_value),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (PaymentRequest.status == STATUS_PAID, base_value),
                        else_=0.0,
                    )
                ),
                0.0,
            ),
        )
        .first()
    )

    if not aggregates:
        return {
            "count": 0,
            "sum_amount": 0.0,
            "sum_finance_amount": 0.0,
            "sum_ready_for_payment": 0.0,
            "sum_paid": 0.0,
        }

    return {
        "count": aggregates[0] or 0,
        "sum_amount": Decimal(str(aggregates[1] or 0)),
        "sum_finance_amount": Decimal(str(aggregates[2] or 0)),
        "sum_ready_for_payment": Decimal(str(aggregates[3] or 0)),
        "sum_paid": Decimal(str(aggregates[4] or 0)),
    }


def _legacy_liabilities_query():
    ledger_aggregate = (
        db.session.query(
            SupplierLedgerEntry.supplier_id.label("supplier_id"),
            func.coalesce(
                func.sum(
                    case(
                        (SupplierLedgerEntry.direction == "debit", SupplierLedgerEntry.amount),
                        (SupplierLedgerEntry.direction == "credit", -SupplierLedgerEntry.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("legacy_balance"),
            func.count(SupplierLedgerEntry.id).label("entry_count"),
        )
        .filter(SupplierLedgerEntry.voided_at.is_(None))
        .group_by(SupplierLedgerEntry.supplier_id)
        .subquery()
    )

    legacy_balance_expr = func.coalesce(ledger_aggregate.c.legacy_balance, 0).label(
        "legacy_balance"
    )
    entry_count_expr = func.coalesce(ledger_aggregate.c.entry_count, 0).label("entry_count")

    q = (
        Supplier.query.outerjoin(
            ledger_aggregate, Supplier.id == ledger_aggregate.c.supplier_id
        )
        .add_columns(legacy_balance_expr, entry_count_expr)
    )

    filters = {
        "q": "",
        "supplier_type": "",
        "min_balance": "",
        "max_balance": "",
    }

    search_query = (request.args.get("q") or "").strip()
    if search_query:
        filters["q"] = search_query
        q = q.filter(func.lower(Supplier.name).like(f"%{search_query.lower()}%"))

    supplier_type = (request.args.get("supplier_type") or "").strip()
    if supplier_type:
        filters["supplier_type"] = supplier_type
        q = q.filter(Supplier.supplier_type == supplier_type)

    min_balance = _safe_float_arg("min_balance")
    if min_balance is not None:
        filters["min_balance"] = (request.args.get("min_balance") or "").strip()
        q = q.filter(legacy_balance_expr >= min_balance)

    max_balance = _safe_float_arg("max_balance")
    if max_balance is not None:
        filters["max_balance"] = (request.args.get("max_balance") or "").strip()
        q = q.filter(legacy_balance_expr <= max_balance)

    supplier_types = [
        row[0]
        for row in db.session.query(Supplier.supplier_type)
        .order_by(Supplier.supplier_type.asc())
        .distinct()
        .all()
    ]

    return (
        q,
        filters,
        supplier_types,
        legacy_balance_expr,
    )


def _paginate_legacy_liabilities(q, order_clause: tuple):
    page = payment_routes._safe_int_arg("page", 1, min_value=1) or 1
    per_page = payment_routes._safe_int_arg("per_page", 20, min_value=1, max_value=100) or 20

    total_count = q.order_by(None).with_entities(func.count(Supplier.id)).scalar() or 0

    ordered_q = q.order_by(*order_clause)
    pagination = ordered_q.paginate(
        page=page, per_page=per_page, error_out=False, count=False
    )
    pagination.total = total_count
    return pagination, page, per_page


def _export_finance_workbench(q, order_clause: tuple):
    total = payment_routes._count_query(q)
    if total > payment_routes.EXPORT_ROW_LIMIT:
        message = (
            f"عدد النتائج ({total}) يتجاوز الحد الأقصى للتصدير ({payment_routes.EXPORT_ROW_LIMIT}). "
            "برجاء تضييق الفلاتر قبل التصدير."
        )
        return Response(
            message,
            status=400,
            mimetype="text/plain; charset=utf-8",
        )

    rows = q.order_by(*order_clause).limit(payment_routes.EXPORT_ROW_LIMIT).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "project",
            "supplier",
            "request_type",
            "status",
            "amount",
            "finance_amount",
            "created_at",
        ]
    )
    for payment_row in rows:
        if isinstance(payment_row, PaymentRequest):
            payment = payment_row
            effective_amount = payment.finance_effective_amount
        else:
            payment = payment_row[0]
            effective_amount = payment_row.finance_effective_amount
        writer.writerow(
            [
                payment.id,
                payment.project.project_name if payment.project else "",
                payment.supplier.name if payment.supplier else "",
                payment.request_type,
                payment.status,
                payment.amount,
                (
                    effective_amount
                    if payment.finance_amount is not None
                    else ""
                ),
                payment_routes._format_ts(payment.created_at),
            ]
        )

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="finance_workbench.csv"'},
    )


@finance_bp.route("/workbench")
@role_required("admin", "finance", "engineering_manager")
def workbench():
    (
        q,
        filters,
        projects,
        suppliers,
        request_types,
        status_filter,
        order_clause,
        effective_amount_expr,
    ) = _finance_workbench_query()
    kpis = _finance_workbench_kpis(q, effective_amount_expr)
    pagination, page, per_page = _paginate_finance_query(q, order_clause)

    query_params = {k: v for k, v in filters.items() if v}
    query_params["status"] = status_filter
    query_params["page"] = page
    query_params["per_page"] = per_page

    export_params = {k: v for k, v in filters.items() if v}
    export_params["status"] = status_filter

    payments = []
    for row in pagination.items:
        if isinstance(row, PaymentRequest):
            payment = row
        else:
            payment = row[0]
            payment._finance_effective_amount = Decimal(
                str(row.finance_effective_amount or 0)
            ).quantize(Decimal("0.01"))
        payments.append(payment)

    return render_template(
        "finance/workbench.html",
        payments=payments,
        pagination=pagination,
        query_params=query_params,
        filters=filters,
        projects=projects,
        suppliers=suppliers,
        request_types=request_types,
        kpis=kpis,
        status_filter=status_filter,
        page_title="Finance Workbench",
        pagination_endpoint="finance.workbench",
        export_endpoint="finance.export_workbench",
        export_params=export_params,
        status_pending=STATUS_PENDING_FIN,
        status_ready=STATUS_READY_FOR_PAYMENT,
        status_paid=STATUS_PAID,
    )


@finance_bp.route("/workbench/export")
@role_required("admin", "finance", "engineering_manager")
def export_workbench():
    q, _, _, _, _, status_filter, order_clause, _ = _finance_workbench_query()
    return _export_finance_workbench(q, order_clause)


@finance_bp.route("/suppliers")
@role_required(*LEGACY_LIABILITY_ROLES)
def legacy_liabilities_directory():
    (
        q,
        filters,
        supplier_types,
        legacy_balance_expr,
    ) = _legacy_liabilities_query()

    order_clause = (legacy_balance_expr.desc(), Supplier.name.asc(), Supplier.id.asc())
    pagination, page, per_page = _paginate_legacy_liabilities(q, order_clause)

    query_params = {
        "q": filters["q"],
        "supplier_type": filters["supplier_type"],
        "min_balance": filters["min_balance"],
        "max_balance": filters["max_balance"],
        "per_page": per_page,
    }

    return render_template(
        "finance/suppliers_legacy_liabilities.html",
        suppliers=pagination.items,
        pagination=pagination,
        page=page,
        per_page=per_page,
        filters=filters,
        supplier_types=supplier_types,
        query_params=query_params,
    )
