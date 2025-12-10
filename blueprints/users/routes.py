# blueprints/users/routes.py

from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user
from extensions import db
from models import User, Role, Project
from permissions import role_required
from . import users_bp


@users_bp.route("/")
@users_bp.route("/list")
@role_required("admin", "dc")
def list_users():
    users = User.query.order_by(User.full_name.asc()).all()
    return render_template("users/list.html", users=users)


@users_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "dc")
def create_user():
    roles = Role.query.order_by(Role.name.asc()).all()
    projects = Project.query.order_by(Project.project_name.asc()).all()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role_id = request.form.get("role_id")
        project_id = request.form.get("project_id")  # يمكن أن يكون فارغاً لبعض الأدوار
        password = (request.form.get("password") or "").strip()
        password_confirm = (request.form.get("password_confirm") or "").strip()
        # حالياً لا نستخدم is_active لأنه غير مخرّن في قاعدة البيانات
        # is_active_flag = bool(request.form.get("is_active"))

        # تحقق أساسي من البيانات
        if not full_name or not email or not role_id or not password:
            flash("من فضلك أدخل جميع البيانات المطلوبة.", "danger")
            return redirect(url_for("users.create_user"))

        if password_confirm and password_confirm != password:
            flash("تأكيد كلمة المرور غير مطابق.", "danger")
            return redirect(url_for("users.create_user"))

        # التحقق من عدم تكرار البريد الإلكتروني
        existing = User.query.filter(User.email == email).first()
        if existing:
            flash("يوجد مستخدم مسجل بنفس البريد الإلكتروني.", "danger")
            return redirect(url_for("users.create_user"))

        # التحقق من ربط المشروع حسب الدور (مهندس / مدير مشروع)
        selected_role = Role.query.get(int(role_id)) if role_id else None
        if selected_role and selected_role.name in ("engineer", "project_manager") and not project_id:
            flash("يجب ربط المهندس أو مدير المشروع بمشروع محدد.", "danger")
            return redirect(url_for("users.create_user"))

        user = User(full_name=full_name, email=email)
        user.role_id = int(role_id)
        user.set_password(password)

        # ربط المشروع إن تم اختياره
        if project_id:
            try:
                user.project_id = int(project_id)
            except ValueError:
                user.project_id = None

        db.session.add(user)
        db.session.commit()
        flash("تم إضافة المستخدم بنجاح.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/create.html", roles=roles, projects=projects)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin", "dc")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    roles = Role.query.order_by(Role.name.asc()).all()
    projects = Project.query.order_by(Project.project_name.asc()).all()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role_id = request.form.get("role_id")
        project_id = request.form.get("project_id")  # يمكن أن يكون فارغاً لبعض الأدوار
        # نفس الفكرة، لا نستخدم is_active حالياً
        # is_active_flag = bool(request.form.get("is_active"))
        new_password = (request.form.get("new_password") or "").strip()

        if not full_name or not email or not role_id:
            flash("من فضلك أدخل جميع البيانات المطلوبة.", "danger")
            return redirect(url_for("users.edit_user", user_id=user.id))

        # التحقق من عدم تكرار البريد الإلكتروني مع مستخدم آخر
        existing = User.query.filter(
            User.email == email,
            User.id != user.id
        ).first()
        if existing:
            flash("يوجد مستخدم آخر مسجل بنفس البريد الإلكتروني.", "danger")
            return redirect(url_for("users.edit_user", user_id=user.id))

        # التحقق من ربط المشروع حسب الدور (مهندس / مدير مشروع)
        selected_role = Role.query.get(int(role_id)) if role_id else None
        if selected_role and selected_role.name in ("engineer", "project_manager") and not project_id:
            flash("يجب ربط المهندس أو مدير المشروع بمشروع محدد.", "danger")
            return redirect(url_for("users.edit_user", user_id=user.id))

        user.full_name = full_name
        user.email = email
        user.role_id = int(role_id)

        # تحديث المشروع المرتبط إن تم اختياره
        if project_id:
            try:
                user.project_id = int(project_id)
            except ValueError:
                user.project_id = None
        else:
            # في حال ترك الحقل فارغاً نلغي الربط
            user.project_id = None

        # تحديث كلمة المرور لو تم إدخال واحدة جديدة
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash("تم تحديث بيانات المستخدم بنجاح.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/edit.html", user=user, roles=roles, projects=projects)


@users_bp.route("/<int:user_id>/delete", methods=["POST"])
@role_required("admin", "dc")
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    # منع المستخدم من حذف نفسه
    if current_user.id == user.id:
        flash("لا يمكن حذف المستخدم الحالي.", "danger")
        return redirect(url_for("users.list_users"))

    db.session.delete(user)
    db.session.commit()
    flash("تم حذف المستخدم بنجاح.", "success")
    return redirect(url_for("users.list_users"))
