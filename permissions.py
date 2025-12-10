# permissions.py
from functools import wraps

from flask import abort, request
from flask_login import current_user, login_required


def role_required(*allowed_roles):
    """
    Decorator لتقييد الوصول على حسب الدور.
    - admin: له صلاحية كاملة على كل شيء دائماً (Full Access).
    - chairman: له صلاحية "قراءة فقط" (GET / HEAD / OPTIONS) على أي راوت.
    - باقي الأدوار: يجب أن تكون ضمن allowed_roles حتى يُسمح لها بالدخول.

    مثال استخدام:
        @role_required("engineering_manager", "project_manager")
        def view():
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            # مستخدم غير مسجّل دخول (المفروض login_required يمنع ذلك)
            if not current_user.is_authenticated:
                abort(401)

            user_role = current_user.role.name if current_user.role else None

            # 1) admin: صلاحيات كاملة دائماً
            if user_role == "admin":
                return view_func(*args, **kwargs)

            # 2) chairman: قراءة فقط لأي راوت
            if user_role == "chairman":
                if request.method in ("GET", "HEAD", "OPTIONS"):
                    return view_func(*args, **kwargs)
                abort(403)

            # 3) لو مفيش دور مربوط بالمستخدم
            if user_role is None:
                abort(403)

            # 4) لو تم تمرير أدوار مسموح بها، يجب أن يكون دور المستخدم ضمنها
            if allowed_roles:
                if user_role not in allowed_roles:
                    abort(403)

            # 5) لو وصلنا هنا، إذن الدور مسموح له بالدخول
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator
