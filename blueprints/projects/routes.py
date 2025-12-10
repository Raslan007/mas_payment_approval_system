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

        # يمكن لاحقاً إضافة تحقق من عدم تكرار الكود إن رغبت
        project = Project(project_name=name, code=code or None)
        db.session.add(project)
        db.session.commit()
        flash("تم إضافة المشروع بنجاح.", "success")
        return redirect(url_for("projects.list_projects"))

    return render_template("projects/create.html")
