# blueprints/suppliers/routes.py

from flask import render_template, request, redirect, url_for, flash
from sqlalchemy import func
from extensions import db
from models import Supplier, normalize_supplier_name
from permissions import role_required
from . import suppliers_bp


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

    return render_template("suppliers/edit.html", supplier=supplier)


@suppliers_bp.route("/<int:supplier_id>/delete", methods=["POST"])
@role_required("admin", "engineering_manager")
def delete_supplier(supplier_id):
    """حذف مورد / مقاول (مسموح فقط للأدمن ومدير الإدارة الهندسية)."""
    supplier = Supplier.query.get_or_404(supplier_id)

    # منع الحذف إذا لديه دفعات مرتبطة
    if getattr(supplier, "payments", None):
        if supplier.payments:
            flash("لا يمكن حذف هذا المورد/المقاول لأنه مرتبط بدفعات.", "danger")
            return redirect(url_for("suppliers.list_suppliers"))

    db.session.delete(supplier)
    db.session.commit()

    flash("تم حذف المورد/المقاول بنجاح.", "success")
    return redirect(url_for("suppliers.list_suppliers"))
