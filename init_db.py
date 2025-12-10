# init_db.py
from app import app
from extensions import db
from models import Role, User


def init_data():
    with app.app_context():
        # إنشاء جميع الجداول في قاعدة البيانات
        db.create_all()

        # إنشاء الأدوار الأساسية لو مش موجودة
        default_roles = [
            "admin",
            "engineering_manager",
            "project_manager",
            "engineer",
            "finance",
        ]

        for role_name in default_roles:
            existing = Role.query.filter_by(name=role_name).first()
            if not existing:
                db.session.add(Role(name=role_name))

        db.session.commit()

        # إنشاء مستخدم أدمن افتراضي لو مش موجود
        admin_email = "admin@mas.com"
        admin = User.query.filter_by(email=admin_email).first()

        if not admin:
            admin_role = Role.query.filter_by(name="admin").first()
            admin = User(
                full_name="System Admin",
                email=admin_email,
                role=admin_role,
            )
            admin.set_password("123123")  # غيّرها بعد أول دخول
            db.session.add(admin)
            db.session.commit()
            print("تم إنشاء مستخدم أدمن افتراضي:")
            print(f"  البريد: {admin_email}")
            print("  كلمة المرور: 123123")
        else:
            print("مستخدم الأدمن موجود بالفعل، لن يتم إنشاؤه مرة أخرى.")


if __name__ == "__main__":
    init_data()
