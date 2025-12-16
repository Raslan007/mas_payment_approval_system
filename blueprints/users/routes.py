# blueprints/users/routes.py

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import inspect

from extensions import db
from models import User, Role, Project, user_projects
from permissions import role_required
from . import users_bp


@users_bp.route("/")
@users_bp.route("/list")
@role_required("admin", "dc")
def list_users():
    users = User.query.order_by(User.full_name.asc()).all()
    return render_template("users/list.html", users=users)


@users_bp.route("/create", methods=["GET", "POST"])
@role_required("admin")
def create_user():
    roles = Role.query.order_by(Role.name.asc()).all()
    projects = Project.query.order_by(Project.project_name.asc()).all()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role_id = request.form.get("role_id")
        project_ids = request.form.getlist("project_ids")
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
        requires_project = selected_role and selected_role.name in (
            "engineer",
            "project_manager",
        )
        if requires_project and not project_ids:
            flash("يجب ربط المهندس أو مدير المشروع بمشروع محدد.", "danger")
            return redirect(url_for("users.create_user"))

        user = User(full_name=full_name, email=email)
        user.role_id = int(role_id)
        user.set_password(password)

        project_ids_int = []
        for pid in project_ids:
            try:
                project_ids_int.append(int(pid))
            except (TypeError, ValueError):
                continue

        if project_ids_int:
            user.project_id = project_ids_int[0]

        if selected_role and selected_role.name == "project_manager":
            user.projects = Project.query.filter(Project.id.in_(project_ids_int)).all()

        db.session.add(user)
        db.session.commit()
        flash("تم إضافة المستخدم بنجاح.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/create.html", roles=roles, projects=projects)


@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    roles = Role.query.order_by(Role.name.asc()).all()
    projects = Project.query.order_by(Project.project_name.asc()).all()

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        role_id = request.form.get("role_id")
        project_ids = request.form.getlist("project_ids")
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
        requires_project = selected_role and selected_role.name in (
            "engineer",
            "project_manager",
        )
        if requires_project and not project_ids:
            flash("يجب ربط المهندس أو مدير المشروع بمشروع محدد.", "danger")
            return redirect(url_for("users.edit_user", user_id=user.id))

        user.full_name = full_name
        user.email = email
        user.role_id = int(role_id)

        project_ids_int = []
        for pid in project_ids:
            try:
                project_ids_int.append(int(pid))
            except (TypeError, ValueError):
                continue

        if project_ids_int:
            user.project_id = project_ids_int[0]
        else:
            user.project_id = None

        if selected_role and selected_role.name == "project_manager":
            user.projects = Project.query.filter(Project.id.in_(project_ids_int)).all()
        else:
            user.projects = []

        # تحديث كلمة المرور لو تم إدخال واحدة جديدة
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash("تم تحديث بيانات المستخدم بنجاح.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/edit.html", user=user, roles=roles, projects=projects)


@users_bp.route("/<int:user_id>/delete", methods=["POST"])
@role_required("admin")
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


def _user_projects_table_exists() -> bool:
    inspector = inspect(db.engine)
    return inspector.has_table("user_projects")


@users_bp.route("/<int:user_id>/projects", methods=["GET", "POST"])
@role_required("admin")
def assign_user_projects(user_id):
    user = User.query.get_or_404(user_id)
    projects = Project.query.order_by(Project.project_name.asc()).all()
    has_user_projects_table = _user_projects_table_exists()

    if request.method == "POST":
        if not has_user_projects_table:
            flash("جدول ربط المستخدمين بالمشاريع غير متاح حاليًا.", "danger")
            return redirect(url_for("users.assign_user_projects", user_id=user.id))

        project_ids_int: list[int] = []
        for pid in request.form.getlist("project_ids"):
            try:
                project_ids_int.append(int(pid))
            except (TypeError, ValueError):
                continue

        unique_project_ids = list(dict.fromkeys(project_ids_int))
        selected_projects = (
            Project.query.filter(Project.id.in_(unique_project_ids))
            .order_by(Project.project_name.asc())
            .all()
            if unique_project_ids
            else []
        )

        user.projects = selected_projects
        db.session.commit()
        flash("تم تحديث المشاريع المرتبطة بالمستخدم بنجاح.", "success")
        return redirect(url_for("users.assign_user_projects", user_id=user.id))

    selected_project_ids: list[int] = []
    if has_user_projects_table:
        selected_project_ids = [
            row.project_id
            for row in db.session.query(user_projects.c.project_id)
            .filter(user_projects.c.user_id == user.id)
            .all()
        ]

    return render_template(
        "users/assign_projects.html",
        user=user,
        projects=projects,
        selected_project_ids=selected_project_ids,
        user_projects_exists=has_user_projects_table,
    )
