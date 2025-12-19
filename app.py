import logging
import os
import time
import uuid

from flask import Flask, g, request
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

from config import Config
from extensions import csrf, db, login_manager
from logging_config import setup_logging
from models import User, ensure_roles, ensure_schema

# استيراد الـ Blueprints
from blueprints.main import main_bp
from blueprints.auth import auth_bp
from blueprints.users import users_bp
from blueprints.projects import projects_bp
from blueprints.suppliers import suppliers_bp
from blueprints.payments import payments_bp
from blueprints.notifications import notifications_bp


def _warn_insecure_defaults(app: Flask) -> None:
    """Emit warnings when sensitive defaults are still in use."""

    secret_key = app.config.get("SECRET_KEY")
    if secret_key == "secret-key-change-me":
        app.logger.warning(
            "SECRET_KEY is using the placeholder value; please set SECRET_KEY "
            "in the environment for production deployments."
        )

    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_uri.startswith("sqlite:///") and "DATABASE_URL" not in os.environ:
        app.logger.warning(
            "DATABASE_URL is not set; application is falling back to the local "
            "SQLite database. Configure a production database via DATABASE_URL."
        )


def _is_production_environment() -> bool:
    """Return True when running in a production-like environment."""

    return os.environ.get("APP_ENV") == "production" or os.environ.get("FLASK_ENV") == "production"


def create_app(config_class=Config) -> Flask:
    """إنشاء وتهيئة تطبيق Flask الرئيسي."""
    app = Flask(__name__)

    # تحميل الإعدادات من Config (ملف config.py)
    app.config.from_object(config_class)

    setup_logging(app)

    _warn_insecure_defaults(app)

    # تهيئة الـ Extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        if app.config.get("AUTO_SCHEMA_BOOTSTRAP"):
            ensure_schema()
        ensure_roles()

    if _is_production_environment():
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # إضافة فلتر لتنسيق الأرقام مع فواصل الآلاف
    def format_number(value, decimals=0):
        """
        استخدامه في القوالب:
            {{ some_number | num }}          -> بدون كسور  40,000
            {{ some_number | num(2) }}       -> مع كسور   40,000.00
        """
        try:
            decimals = int(decimals)
        except (TypeError, ValueError):
            decimals = 0

        try:
            format_str = f"{{:,.{decimals}f}}"
            return format_str.format(float(value))
        except (TypeError, ValueError):
            return value

    app.jinja_env.filters["num"] = format_number

    # إعدادات الـ LoginManager
    login_manager.login_view = "auth.login"
    login_manager.login_message = "يجب تسجيل الدخول أولاً."
    login_manager.login_message_category = "warning"

    # تحميل المستخدم في جلسة تسجيل الدخول
    @login_manager.user_loader
    def load_user(user_id: str):
        """
        تحميل المستخدم من الـ session.

        تم استخدام db.session.get بدلاً من Query.get
        لتكون متوافقة مع SQLAlchemy 2 وتجنب التحذير:
        LegacyAPIWarning: The Query.get() method is considered legacy.
        """
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        # أسلوب SQLAlchemy 2 الموصى به
        return db.session.get(User, user_id_int)

    def _log_request_summary(status_code: int) -> None:
        request_id = getattr(g, "request_id", None)
        start_time = getattr(g, "request_start_time", None)

        duration_ms = None
        if start_time is not None:
            duration_ms = (time.perf_counter() - start_time) * 1000

        app.logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "status_code": status_code,
                "duration_ms": int(duration_ms) if duration_ms is not None else None,
            },
        )

    @app.before_request
    def attach_request_context() -> None:
        g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        g.request_start_time = time.perf_counter()

    @app.after_request
    def append_request_id(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        _log_request_summary(response.status_code)
        return response

    @app.errorhandler(Exception)
    def handle_exception(error):
        if not isinstance(error, HTTPException):
            app.logger.exception("Unhandled exception", exc_info=error)

        response = error.get_response() if isinstance(error, HTTPException) else app.make_response(("Internal Server Error", 500))
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        return response

    # تسجيل الـ Blueprints
    app.register_blueprint(main_bp)                              # /
    app.register_blueprint(auth_bp, url_prefix="/auth")          # /auth/...
    app.register_blueprint(users_bp, url_prefix="/users")        # /users/...
    app.register_blueprint(projects_bp, url_prefix="/projects")  # /projects/...
    app.register_blueprint(suppliers_bp, url_prefix="/suppliers")  # /suppliers/...
    app.register_blueprint(payments_bp, url_prefix="/payments")  # /payments/...
    app.register_blueprint(notifications_bp, url_prefix="/notifications")  # /notifications/...

    return app


# إنشاء التطبيق وتشغيله مباشرة عند استدعاء python app.py
app = create_app()

if __name__ == "__main__":
    # في حالة النشر على سيرفر داخلي وتريد الوصول من أجهزة أخرى، يمكنك تغيير
    # متغير البيئة FLASK_RUN_HOST إلى "0.0.0.0".
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        debug=app.config.get("DEBUG", False),
    )
