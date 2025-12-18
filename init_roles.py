# init_roles.py
from app import app
from models import ensure_roles


if __name__ == "__main__":
    with app.app_context():
        ensure_roles()
        print("تم التأكد من وجود جميع الأدوار (Roles) في قاعدة البيانات.")
