# extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# تهيئة الـ SQLAlchemy (قاعدة البيانات)
db = SQLAlchemy()

# تهيئة الـ LoginManager (إدارة تسجيل الدخول)
login_manager = LoginManager()

# اسم الـ view المسؤولة عن تسجيل الدخول
login_manager.login_view = "auth.login"

# الرسالة الافتراضية لو حد حاول يدخل صفحة محمية بدون تسجيل دخول
login_manager.login_message = "من فضلك سجل الدخول أولاً"
