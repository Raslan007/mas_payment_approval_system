# blueprints/projects/routes.py

from flask import render_template, request, redirect, url_for, flash
from extensions import db
from models import Project
from permissions import role_required
from . import projects_bp


@projects_bp.route("/")
@projects_bp.route("/list")
@role_required("admin", "engineering_manager", "dc")
def list_projects():
    projects = Project.query.order_by(Project.project_name.asc()).all()
    return render_template("projects/list.html", projects=projects)


@projects_bp.route("/create", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "dc")
def create_project():
    if request.method == "POST":
        name = (request.form.get("project_name") or "").strip()
        code = (request.form.get("code") or "").strip()

        if not name:
            flash("من فضلك أدخل اسم المشروع.", "danger")
            return redirect(url_for("projects.create_project"))

        # التحقق الاختياري من عدم تكرار الكود
        if code:
            existing = Project.query.filter(Project.code == code).first()
            if existing:
                flash("يوجد مشروع آخر مسجل بنفس كود المشروع.", "danger")
                return redirect(url_for("projects.create_project"))

        project = Project(project_name=name, code=code or None)
        db.session.add(project)
        db.session.commit()
        flash("تم إضافة المشروع بنجاح.", "success")
        return redirect(url_for("projects.list_projects"))

    return render_template("projects/create.html")


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@role_required("admin", "engineering_manager", "dc")
def edit_project(project_id):
    """تعديل بيانات مشروع قائم."""
    project = Project.query.get_or_404(project_id)

    if request.method == "POST":
        name = (request.form.get("project_name") or "").strip()
        code = (request.form.get("code") or "").strip()

        if not name:
            flash("من فضلك أدخل اسم المشروع.", "danger")
            return redirect(url_for("projects.edit_project", project_id=project.id))

        # التحقق من عدم تكرار الكود مع مشروع آخر
        if code:
            existing = Project.query.filter(
                Project.code == code,
                Project.id != project.id
            ).first()
            if existing:
                flash("يوجد مشروع آخر مسجل بنفس كود المشروع.", "danger")
                return redirect(url_for("projects.edit_project", project_id=project.id))

        project.project_name = name
        project.code = code or None

        db.session.commit()
        flash("تم تحديث بيانات المشروع بنجاح.", "success")
        return redirect(url_for("projects.list_projects"))

    return render_template("projects/edit.html", project=project)
