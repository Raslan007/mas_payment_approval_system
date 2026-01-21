# blueprints/suppliers/routes.py

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from extensions import db
from models import Supplier, SupplierLedgerEntry, Project, normalize_supplier_name
from permissions import role_required
from . import suppliers_bp


LEDGER_VIEW_ROLES = {
    "admin",
    "engineering_manager",
    "procurement",
    "accounts",
    "chairman",
    "finance",
}
LEDGER_EDIT_ROLES = {"admin", "finance"}

def _quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _parse_amount(value: str) -> Decimal | None:
    try:
        return _quantize_amount(Decimal(value))
    except (InvalidOperation, TypeError):
        return None


def _ledger_context(supplier: Supplier):
    ledger_entries = (
        SupplierLedgerEntry.query.options(
            selectinload(SupplierLedgerEntry.project),
            selectinload(SupplierLedgerEntry.created_by),
            selectinload(SupplierLedgerEntry.voided_by),
        )
        .filter(SupplierLedgerEntry.supplier_id == supplier.id)
        .order_by(
            SupplierLedgerEntry.entry_date.desc(),
            SupplierLedgerEntry.created_at.desc(),
            SupplierLedgerEntry.id.desc(),
        )
        .all()
    )
    projects = Project.query.order_by(Project.project_name.asc()).all()
    return {
        "ledger_entries": ledger_entries,
        "legacy_balance": supplier.legacy_balance,
        "projects": projects,
        "today": date.today(),
    }


@suppliers_bp.route("/")
@suppliers_bp.route("/list")
@role_required("admin", "engineering_manager", "dc")
def list_suppliers():
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
        Supplier.query.order_by(Supplier.name.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "suppliers/list.html",
        suppliers=pagination.items,
        pagination=pagination,
        page=page,
        per_page=per_page,
    )


@suppliers_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "dc")
def create_supplier():
    if request.method == "POST":
        name = normalize_supplier_name(request.form.get("name") or "")
        supplier_type = (request.form.get("supplier_type") or "").strip()

        if not name or not supplier_type:
            flash("من فضلك أدخل اسم المورد/المقاول ونوعه.", "danger")
            return redirect(url_for("suppliers.create_supplier"))

        # يمكن لاحقًا إضافة تحقق من التكرار (نفس الاسم + النوع)
        existing = Supplier.query.filter(
            func.lower(Supplier.name) == name.lower(),
        ).first()
        if existing:
            flash("يوجد مورد/مقاول مسجل بنفس الاسم.", "danger")
            return redirect(url_for("suppliers.create_supplier"))

        supplier = Supplier(name=name, supplier_type=supplier_type)
        db.session.add(supplier)
        db.session.commit()
        flash("تم إضافة المورد/المقاول بنجاح.", "success")
        return redirect(url_for("suppliers.list_suppliers"))

    return render_template("suppliers/create.html")


@suppliers_bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "dc")
def edit_supplier(supplier_id):
    """تعديل بيانات مورد / مقاول."""
    supplier = Supplier.query.get_or_404(supplier_id)

    if request.method == "POST":
        name = normalize_supplier_name(request.form.get("name") or "")
        supplier_type = (request.form.get("supplier_type") or "").strip()

        if not name or not supplier_type:
            flash("من فضلك أدخل اسم المورد/المقاول ونوعه.", "danger")
            return redirect(url_for("suppliers.edit_supplier", supplier_id=supplier.id))

        # التحقق من عدم وجود مورد آخر بنفس الاسم والنوع
        existing = Supplier.query.filter(
            func.lower(Supplier.name) == name.lower(),
            Supplier.id != supplier.id,
        ).first()
        if existing:
            flash("يوجد مورد/مقاول آخر مسجل بنفس الاسم.", "danger")
            return redirect(url_for("suppliers.edit_supplier", supplier_id=supplier.id))

        supplier.name = name
        supplier.supplier_type = supplier_type

        db.session.commit()
        flash("تم تحديث بيانات المورد/المقاول بنجاح.", "success")
        return redirect(url_for("suppliers.list_suppliers"))

    can_view_ledger = bool(current_user.role and current_user.role.name in LEDGER_VIEW_ROLES)
    ledger_context = _ledger_context(supplier) if can_view_ledger else {}
    return render_template(
        "suppliers/edit.html",
        supplier=supplier,
        can_view_ledger=can_view_ledger,
        **ledger_context,
    )


@suppliers_bp.route("/<int:supplier_id>/delete", methods=["POST"])
@role_required("admin", "engineering_manager")
def delete_supplier(supplier_id):
    """حذف مورد / مقاول (مسموح فقط للأدمن ومدير الإدارة الهندسية)."""
    supplier = Supplier.query.get_or_404(supplier_id)

    # منع الحذف إذا لديه دفعات مرتبطة
    if supplier.payment_requests:
        flash("لا يمكن حذف هذا المورد/المقاول لأنه مرتبط بدفعات.", "danger")
        return redirect(url_for("suppliers.list_suppliers"))

    if supplier.ledger_entries:
        flash("لا يمكن حذف هذا المورد/المقاول لأنه مرتبط بسجل التزامات.", "danger")
        return redirect(url_for("suppliers.list_suppliers"))

    db.session.delete(supplier)
    db.session.commit()

    flash("تم حذف المورد/المقاول بنجاح.", "success")
    return redirect(url_for("suppliers.list_suppliers"))


@suppliers_bp.route("/<int:supplier_id>/ledger", methods=["GET"])
@role_required("admin", "engineering_manager", "procurement", "accounts", "chairman", "finance")
def view_ledger(supplier_id):
    supplier = Supplier.query.get_or_404(supplier_id)
    return render_template(
        "suppliers/ledger.html",
        supplier=supplier,
        can_view_ledger=True,
        **_ledger_context(supplier),
    )


@suppliers_bp.route("/<int:supplier_id>/ledger/opening-balance", methods=["POST"])
@role_required("admin", "finance")
def create_opening_balance(supplier_id):
    supplier = Supplier.query.get_or_404(supplier_id)
    if not (current_user.role and current_user.role.name in LEDGER_EDIT_ROLES):
        flash("ليست لديك صلاحية لإضافة رصيد افتتاحي.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    amount = _parse_amount(request.form.get("amount", "").strip())
    entry_date_raw = request.form.get("entry_date") or ""
    note = (request.form.get("note") or "").strip() or None
    project_id = request.form.get("project_id", type=int)

    if not amount or amount <= 0:
        flash("من فضلك أدخل مبلغًا صحيحًا أكبر من صفر.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    try:
        entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        flash("من فضلك أدخل تاريخًا صحيحًا.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        project_id=project_id,
        entry_type="opening_balance",
        direction="debit",
        amount=amount,
        entry_date=entry_date,
        note=note,
        created_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    flash("تم إضافة رصيد افتتاحي للمورد.", "success")
    return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))


@suppliers_bp.route("/<int:supplier_id>/ledger/adjustment", methods=["POST"])
@role_required("admin", "finance")
def create_adjustment(supplier_id):
    supplier = Supplier.query.get_or_404(supplier_id)
    if not (current_user.role and current_user.role.name in LEDGER_EDIT_ROLES):
        flash("ليست لديك صلاحية لإضافة تسوية.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    direction = (request.form.get("direction") or "").strip().lower()
    amount = _parse_amount(request.form.get("amount", "").strip())
    entry_date_raw = request.form.get("entry_date") or ""
    note = (request.form.get("note") or "").strip() or None
    project_id = request.form.get("project_id", type=int)

    if direction not in {"debit", "credit"}:
        flash("من فضلك اختر اتجاهًا صحيحًا (مدين/دائن).", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    if not amount or amount <= 0:
        flash("من فضلك أدخل مبلغًا صحيحًا أكبر من صفر.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    try:
        entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        flash("من فضلك أدخل تاريخًا صحيحًا.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        project_id=project_id,
        entry_type="adjustment",
        direction=direction,
        amount=amount,
        entry_date=entry_date,
        note=note,
        created_by_id=current_user.id,
    )
    db.session.add(entry)
    db.session.commit()
    flash("تم إضافة تسوية على رصيد المورد.", "success")
    return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))


@suppliers_bp.route("/<int:supplier_id>/ledger/<int:entry_id>/void", methods=["POST"])
@role_required("admin", "finance")
def void_ledger_entry(supplier_id, entry_id):
    supplier = Supplier.query.get_or_404(supplier_id)
    entry = SupplierLedgerEntry.query.filter(
        SupplierLedgerEntry.id == entry_id,
        SupplierLedgerEntry.supplier_id == supplier.id,
    ).first_or_404()

    if not (current_user.role and current_user.role.name in LEDGER_EDIT_ROLES):
        flash("ليست لديك صلاحية لإلغاء القيد.", "danger")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    if entry.voided_at:
        flash("تم إلغاء هذا القيد بالفعل.", "warning")
        return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))

    entry.voided_at = datetime.utcnow()
    entry.voided_by_id = current_user.id
    db.session.commit()
    flash("تم إلغاء القيد بنجاح.", "success")
    return redirect(url_for("suppliers.view_ledger", supplier_id=supplier.id))
