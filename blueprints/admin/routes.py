from __future__ import annotations

from typing import Iterable

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Project, User
from permissions import role_required
from project_scopes import _current_user_projects_table
from . import admin_bp

ALLOWED_SCOPED_ROLES: tuple[str, ...] = ("project_manager", "project_engineer")


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_projects(project_ids: Iterable[int]) -> list[int]:
    unique_ids = list(dict.fromkeys(pid for pid in project_ids if isinstance(pid, int)))
    if not unique_ids:
        return []

    existing_ids = {
        pid for (pid,) in db.session.query(Project.id).filter(Project.id.in_(unique_ids)).all()
    }
    return [pid for pid in unique_ids if pid in existing_ids]


def _fetch_current_assignments(user_id: int, scoped_role: str | None) -> list[int]:
    table, has_role_column = _current_user_projects_table()
    if table is None:
        return []

    query = db.session.query(table.c.project_id).filter(table.c.user_id == user_id)
    if has_role_column and scoped_role:
        query = query.filter(table.c.scoped_role == scoped_role)

    return [
        row.project_id
        for row in query.all()
        if row.project_id is not None
    ]


def _replace_assignments(user_id: int, scoped_role: str, project_ids: list[int]) -> None:
    table, has_role_column = _current_user_projects_table()
    if table is None:
        raise RuntimeError("جدول user_projects غير متاح.")

    desired_ids = list(dict.fromkeys(pid for pid in project_ids if isinstance(pid, int)))
    desired_set = set(desired_ids)

    delete_stmt = table.delete().where(table.c.user_id == user_id)
    if has_role_column:
        delete_stmt = delete_stmt.where(table.c.scoped_role == scoped_role)
    if desired_set:
        delete_stmt = delete_stmt.where(~table.c.project_id.in_(desired_set))
    db.session.execute(delete_stmt)

    if desired_ids:
        existing_query = (
            db.session.query(table.c.project_id)
            .filter(table.c.user_id == user_id)
            .filter(table.c.project_id.in_(desired_ids))
        )
        if has_role_column:
            existing_query = existing_query.filter(table.c.scoped_role == scoped_role)
        existing_ids = {row.project_id for row in existing_query.all()}
    else:
        existing_ids = set()

    missing_ids = [pid for pid in desired_ids if pid not in existing_ids]
    if missing_ids:
        insert_rows = []
        for pid in missing_ids:
            row = {"user_id": user_id, "project_id": pid}
            if has_role_column:
                row["scoped_role"] = scoped_role
            insert_rows.append(row)
        db.session.execute(table.insert(), insert_rows)


@admin_bp.route("/project-assignments", methods=["GET", "POST"])
@role_required("admin")
def project_assignments():
    users = User.query.order_by(User.full_name.asc()).all()
    projects = Project.query.order_by(Project.project_name.asc()).all()
    table, _ = _current_user_projects_table()
    has_mapping_table = table is not None

    selected_user_id = _safe_int(request.values.get("user_id"))
    selected_role = request.values.get("scoped_role") or ALLOWED_SCOPED_ROLES[0]
    if selected_role not in ALLOWED_SCOPED_ROLES:
        selected_role = ALLOWED_SCOPED_ROLES[0]

    selected_user = User.query.get(selected_user_id) if selected_user_id else None

    if request.method == "POST":
        if not has_mapping_table:
            flash("جدول ربط المشاريع غير متاح حالياً.", "danger")
            return redirect(url_for("admin.project_assignments"))

        if selected_user is None:
            flash("يجب اختيار مستخدم صالح.", "danger")
            return redirect(url_for("admin.project_assignments"))

        if request.form.get("scoped_role") not in ALLOWED_SCOPED_ROLES:
            flash("يجب اختيار دور صالح.", "danger")
            return redirect(url_for("admin.project_assignments"))

        project_ids_raw = request.form.getlist("project_ids")
        project_ids = []
        for pid in project_ids_raw:
            pid_int = _safe_int(pid)
            if pid_int is not None:
                project_ids.append(pid_int)

        valid_project_ids = _validate_projects(project_ids)
        if project_ids and len(valid_project_ids) != len(set(project_ids)):
            flash("المشروعات المحددة غير صالحة.", "danger")
            return redirect(url_for("admin.project_assignments"))

        try:
            _replace_assignments(selected_user.id, selected_role, valid_project_ids)
            if valid_project_ids:
                selected_user.project_id = valid_project_ids[0]
            db.session.commit()
        except (SQLAlchemyError, RuntimeError) as exc:
            db.session.rollback()
            flash("حدث خطأ أثناء تحديث التعيينات. لم يتم حفظ أي تغييرات.", "danger")
            current_app.logger.exception("failed to update assignments", exc_info=exc)
            return redirect(url_for("admin.project_assignments"))

        flash("تم تحديث تعيينات المشاريع بنجاح.", "success")
        return redirect(
            url_for(
                "admin.project_assignments",
                user_id=selected_user.id,
                scoped_role=selected_role,
            )
        )

    selected_project_ids = (
        _fetch_current_assignments(selected_user.id, selected_role)
        if selected_user
        else []
    )

    return render_template(
        "admin/project_assignments.html",
        users=users,
        projects=projects,
        selected_user=selected_user,
        selected_role=selected_role,
        selected_project_ids=selected_project_ids,
        allowed_roles=ALLOWED_SCOPED_ROLES,
        has_mapping_table=has_mapping_table,
    )
