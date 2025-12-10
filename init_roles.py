# init_roles.py
from app import app
from extensions import db
from models import Role


def ensure_roles():
    roles_data = [
        ("admin", "مدير النظام"),
        ("engineering_manager", "مدير الإدارة الهندسية"),
        ("project_manager", "مدير مشروع"),
        ("engineer", "مهندس موقع"),
        ("finance", "المالية"),
        ("chairman", "رئيس مجلس الإدارة"),
        ("dc", "Data Entry / Data Control"),
    ]

    with app.app_context():
        for name, desc in roles_data:
            role = Role.query.filter_by(name=name).first()
            if not role:
                role = Role(name=name)
                # لو عندك عمود description في Role سيتم حفظه، ولو مش موجود يتجاهله SQLAlchemy
                try:
                    role.description = desc
                except Exception:
                    pass

                db.session.add(role)

        db.session.commit()
        print("تم التأكد من وجود جميع الأدوار (Roles) في قاعدة البيانات.")


if __name__ == "__main__":
    ensure_roles()
