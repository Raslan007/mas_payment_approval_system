from __future__ import annotations

from typing import Any

from flask import url_for
from werkzeug.routing import BuildError

Module = dict[str, Any]

DEFAULT_ICON = "fa-solid fa-grid-2"


MODULE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "notifications",
        "title": "الإشعارات",
        "description": "رسائل وتنبيهات النظام الموجهة لك",
        "icon": "fa-regular fa-bell",
        "endpoint": "notifications.list_notifications",
        "badge": "notifications",
    },
    {
        "key": "overview",
        "title": "نظرة إجمالية",
        "description": "رؤية شاملة لمؤشرات الدفعات والمشروعات",
        "icon": "fa-solid fa-chart-pie",
        "endpoint": "main.overview",
        "roles": {"admin", "engineering_manager", "chairman", "finance"},
    },
    {
        "key": "payments",
        "title": "الدفعات",
        "description": "إدارة طلبات الدفعات ومراحل الاعتماد",
        "icon": "fa-solid fa-wallet",
        "endpoint": "payments.index",
        "exclude_roles": {"dc"},
    },
    {
        "key": "purchase_orders",
        "title": "أوامر الشراء",
        "description": "متابعة أوامر الشراء الخاصة بالمشتريات",
        "icon": "fa-solid fa-cart-shopping",
        "endpoint": "purchase_orders.index",
        "roles": {
            "procurement",
            "admin",
            "finance",
            "engineering_manager",
            "project_manager",
            "engineer",
        },
    },
    {
        "key": "ready_for_payment",
        "title": "جاهزة للصرف",
        "description": "دفعات معتمدة تنتظر التسجيل المالي",
        "icon": "fa-solid fa-circle-check",
        "endpoint": "payments.finance_eng_approved",
        "roles": {"finance", "admin", "engineering_manager"},
    },
    {
        "key": "eng_dashboard",
        "title": "لوحة الإدارة الهندسية",
        "description": "متابعة الدفعات عبر الإدارة الهندسية",
        "icon": "fa-solid fa-sitemap",
        "endpoint": "main.eng_dashboard",
        "roles": {"admin", "engineering_manager", "chairman"},
    },
    {
        "key": "eng_commitments",
        "title": "الالتزامات / أوامر الشراء",
        "description": "متابعة أوامر الشراء والالتزامات المالية",
        "icon": "fa-solid fa-clipboard-list",
        "endpoint": "main.eng_commitments",
        "roles": {"admin", "engineering_manager", "chairman"},
    },
    {
        "key": "projects",
        "title": "المشروعات",
        "description": "إدارة قائمة المشروعات والمعلومات الأساسية",
        "icon": "fa-solid fa-building",
        "endpoint": "projects.list_projects",
        "roles": {"admin", "engineering_manager", "chairman", "dc"},
    },
    {
        "key": "suppliers",
        "title": "الموردون / المقاولون",
        "description": "تتبع بيانات الموردين وتحديثها",
        "icon": "fa-solid fa-users",
        "endpoint": "suppliers.list_suppliers",
        "roles": {"admin", "engineering_manager", "chairman", "dc"},
    },
    {
        "key": "users",
        "title": "المستخدمون",
        "description": "إدارة صلاحيات وحسابات المستخدمين",
        "icon": "fa-solid fa-user-gear",
        "endpoint": "users.list_users",
        "roles": {"admin", "dc"},
    },
    {
        "key": "project_assignments",
        "title": "تعيينات المشروعات",
        "description": "ربط المستخدمين بالمشروعات حسب الدور",
        "icon": "fa-solid fa-diagram-project",
        "endpoint": "admin.project_assignments",
        "roles": {"admin"},
    },
    {
        "key": "finance_workbench",
        "title": "لوحة الحسابات",
        "description": "تصدير بيانات وتقارير سريعة",
        "icon": "fa-solid fa-file-export",
        "endpoint": "finance.workbench",
        "roles": {"admin", "finance", "engineering_manager"},
    },
]


def _safe_url_for(endpoint: str | None, endpoint_kwargs: dict[str, Any] | None = None) -> str | None:
    if not endpoint:
        return None

    try:
        return url_for(endpoint, **(endpoint_kwargs or {}))
    except (BuildError, RuntimeError):
        return None


def _user_role_name(user: Any) -> str:
    role = getattr(user, "role", None)
    return role.name if role else ""


def _is_authenticated(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def _get_notifications_count(user: Any) -> int:
    if not _is_authenticated(user):
        return 0

    notifications = getattr(user, "notifications", None)
    if notifications is None:
        return 0

    return notifications.filter_by(is_read=False).count()


def get_launcher_modules(user: Any) -> list[Module]:
    """
    Build the list of launcher modules for the current user.

    Each module dictionary contains:
        - title: Arabic title to render.
        - description: Optional description for tiles.
        - icon: Font Awesome icon classes (with a fallback default).
        - url: Resolved URL for the endpoint.
        - badge: Optional badge value (e.g., notifications count).
    """

    role_name = _user_role_name(user)
    notifications_count = _get_notifications_count(user)
    is_auth = _is_authenticated(user)

    modules: list[Module] = []
    for definition in MODULE_DEFINITIONS:
        roles = definition.get("roles")
        exclude_roles = definition.get("exclude_roles")

        if roles and role_name not in roles:
            continue

        if exclude_roles and role_name in exclude_roles:
            continue

        if not is_auth:
            continue

        url = _safe_url_for(definition.get("endpoint"), definition.get("endpoint_kwargs"))
        if not url:
            continue

        badge_value = definition.get("badge")
        if badge_value == "notifications":
            badge_value = notifications_count

        module_icon = definition.get("icon") or DEFAULT_ICON

        module: Module = {
            "key": definition["key"],
            "title": definition["title"],
            "description": definition.get("description"),
            "icon": module_icon,
            "url": url,
        }

        if badge_value:
            module["badge"] = badge_value

        modules.append(module)

    return modules
