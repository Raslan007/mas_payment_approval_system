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

        supplier = Supplier(name=name, supplier_type=supplier_type)
        db.session.add(supplier)
        db.session.commit()
        flash("تم إضافة المورد/المقاول بنجاح.", "success")
        return redirect(url_for("suppliers.list_suppliers"))

    return render_template("suppliers/create.html")
