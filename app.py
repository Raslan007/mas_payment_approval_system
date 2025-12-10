# app.py
from flask import Flask

from config import Config
from extensions import db, login_manager
from models import User

# استيراد الـ Blueprints
from blueprints.main import main_bp
from blueprints.auth import auth_bp
from blueprints.users import users_bp
from blueprints.projects import projects_bp
from blueprints.suppliers import suppliers_bp
from blueprints.payments import payments_bp


def create_app(config_class=Config) -> Flask:
    """إنشاء وتهيئة تطبيق Flask الرئيسي."""
    app = Flask(__name__)

    # تحميل الإعدادات من Config (ملف config.py)
    app.config.from_object(config_class)

    # تهيئة الـ Extensions
    db.init_app(app)
    login_manager.init_app(app)

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

    # تسجيل الـ Blueprints
    app.register_blueprint(main_bp)                              # /
    app.register_blueprint(auth_bp, url_prefix="/auth")          # /auth/...
    app.register_blueprint(users_bp, url_prefix="/users")        # /users/...
    app.register_blueprint(projects_bp, url_prefix="/projects")  # /projects/...
    app.register_blueprint(suppliers_bp, url_prefix="/suppliers")  # /suppliers/...
    app.register_blueprint(payments_bp, url_prefix="/payments")  # /payments/...

    return app


# إنشاء التطبيق وتشغيله مباشرة عند استدعاء python app.py
app = create_app()

if __name__ == "__main__":
    # في حالة النشر على سيرفر داخلي وتريد الوصول من أجهزة أخرى:
    # يمكنك تغيير السطر إلى:
    # app.run(host="0.0.0.0", debug=True)
    app.run(debug=True)
