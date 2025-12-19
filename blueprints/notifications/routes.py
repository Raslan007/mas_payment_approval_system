# blueprints/notifications/routes.py

from flask import render_template, redirect, url_for, abort
from flask_login import login_required, current_user

from . import notifications_bp
from extensions import db
from models import Notification


@notifications_bp.route("/")
@login_required
def list_notifications():
    """
    عرض كل الإشعارات الخاصة بالمستخدم الحالي.
    الأحدث في الأعلى.
    """
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("notifications/list.html", notifications=notifications)


@notifications_bp.route("/<int:notification_id>/read", methods=["POST"])
@login_required
def read_notification(notification_id):
    """
    تعليم إشعار كمقروء والانتقال للرابط المرتبط به إن وجد.
    """
    notification = Notification.query.get_or_404(notification_id)

    if notification.user_id != current_user.id:
        abort(403)

    notification.is_read = True
    db.session.commit()

    if notification.url:
        return redirect(notification.url)

    return redirect(url_for("notifications.list_notifications"))


@notifications_bp.route("/read_all", methods=["POST"])
@login_required
def mark_all_read():
    """
    تعليم كل إشعارات المستخدم الحالي كمقروءة.
    """
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update(
        {"is_read": True}
    )
    db.session.commit()
    return redirect(url_for("notifications.list_notifications"))
