# blueprints/suppliers/routes.py

from flask import render_template, request, redirect, url_for, flash
from extensions import db
from models import Supplier
from permissions import role_required
from . import suppliers_bp


@suppliers_bp.route("/")
@suppliers_bp.route("/list")
@role_required("admin", "engineering_manager", "dc")
def list_suppliers():
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    return render_template("suppliers/list.html", suppliers=suppliers)


@suppliers_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "dc")
def create_supplier():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        supplier_type = (request.form.get("supplier_type") or "").strip()

        if not name or not supplier_type:
            flash("من فضلك أدخل اسم المورد/المقاول ونوعه.", "danger")
            return redirect(url_for("suppliers.create_supplier"))

        # يمكن لاحقًا إضافة تحقق من التكرار (نفس الاسم + النوع)
        existing = Supplier.query.filter(
            Supplier.name == name,
            Supplier.supplier_type == supplier_type,
        ).first()
        if existing:
            flash("يوجد مورد/مقاول مسجل بنفس الاسم والنوع.", "danger")
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
        name = (request.form.get("name") or "").strip()
        supplier_type = (request.form.get("supplier_type") or "").strip()

        if not name or not supplier_type:
            flash("من فضلك أدخل اسم المورد/المقاول ونوعه.", "danger")
            return redirect(url_for("suppliers.edit_supplier", supplier_id=supplier.id))

        # التحقق من عدم وجود مورد آخر بنفس الاسم والنوع
        existing = Supplier.query.filter(
            Supplier.name == name,
            Supplier.supplier_type == supplier_type,
            Supplier.id != supplier.id,
        ).first()
        if existing:
            flash("يوجد مورد/مقاول آخر مسجل بنفس الاسم والنوع.", "danger")
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
