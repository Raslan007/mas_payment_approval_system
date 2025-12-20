import re
from html.parser import HTMLParser

import pytest

from app import create_app
from config import Config
from extensions import db
from models import Notification, Role, User


class DashboardAccessConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app_context():
    app = create_app(DashboardAccessConfig)
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture()
def client(app_context):
    return app_context.test_client()


@pytest.fixture()
def roles():
    role_names = [
        "admin",
        "engineering_manager",
        "finance",
        "engineer",
        "project_manager",
        "dc",
        "chairman",
        "payment_notifier",
    ]
    role_objects = {name: Role(name=name) for name in role_names}
    db.session.add_all(role_objects.values())
    db.session.commit()
    return role_objects


@pytest.fixture()
def user_factory(roles):
    def _create_user(role_name: str) -> User:
        role = roles[role_name]
        user = User(full_name=role_name, email=f"{role_name}@example.com", role=role)
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    return _create_user


@pytest.fixture()
def login(client):
    def _login(user: User):
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    return _login


@pytest.mark.parametrize(
    ("role_name", "expected_status"),
    [
        (None, 302),
        ("engineer", 200),
        ("engineering_manager", 200),
        ("finance", 200),
        ("chairman", 200),
        ("dc", 200),
        ("payment_notifier", 200),
    ],
)
def test_dashboard_access_matrix(client, user_factory, login, role_name, expected_status):
    if role_name:
        login(user_factory(role_name))

    response = client.get("/dashboard")
    assert response.status_code == expected_status
    if role_name is None:
        assert "/auth/login" in response.headers.get("Location", "")


def test_dashboard_ui_elements_present(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    assert "tile-grid" in body
    assert 'class="tile-card' in body
    assert "user-menu-toggle" in body


def test_dashboard_includes_local_fontawesome(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "/static/vendor/fontawesome/css/all.min.css" in response.get_data(
        as_text=True
    )


def test_fontawesome_webfont_served(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/static/vendor/fontawesome/webfonts/fa-solid-900.woff2")

    assert response.status_code == 200
    assert response.data


class _TileIconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[str] = []
        self._in_tile_icon = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        class_attr = attr_dict.get("class", "")
        if tag == "div" and "tile-icon" in class_attr:
            self._in_tile_icon = True
            return

        if tag == "i" and self._in_tile_icon:
            icon_class = attr_dict.get("class", "")
            if icon_class:
                self.icons.append(icon_class)

    def handle_endtag(self, tag):
        if tag == "div" and self._in_tile_icon:
            self._in_tile_icon = False


def test_tiles_render_icon_elements(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/dashboard")
    parser = _TileIconParser()
    parser.feed(response.get_data(as_text=True))

    assert parser.icons, "Expected at least one tile icon to render"
    assert all("fa-" in icon for icon in parser.icons)


class _TileLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attr_dict = dict(attrs)
        class_attr = attr_dict.get("class", "")
        if "tile-card" in class_attr:
            href = attr_dict.get("href")
            if href:
                self.hrefs.append(href)


class _DropdownIconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[str] = []
        self._in_dropdown_item = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        class_attr = attr_dict.get("class", "")

        if tag == "a" and "dropdown-item" in class_attr:
            self._in_dropdown_item = True
            return

        if tag == "i" and self._in_dropdown_item:
            icon_class = attr_dict.get("class", "")
            if icon_class:
                self.icons.append(icon_class)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_dropdown_item:
            self._in_dropdown_item = False


def test_tile_links_resolve(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/dashboard")
    parser = _TileLinkParser()
    parser.feed(response.get_data(as_text=True))

    assert parser.hrefs, "Expected at least one tile link"
    for href in parser.hrefs:
        inner_response = client.get(href)
        assert inner_response.status_code in {200, 302}
        assert inner_response.status_code != 404


def test_counters_default_to_zero(client, user_factory, login):
    user = user_factory("admin")
    # ensure no notifications or messages exist
    Notification.query.delete()
    db.session.commit()

    login(user)
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    counters = re.findall(r'class="counter">(\d+)<', body)
    assert counters
    assert all(count == "0" for count in counters)


@pytest.mark.parametrize(
    ("role_name", "expected_status"),
    [
        (None, 302),
        ("admin", 200),
        ("engineering_manager", 200),
        ("finance", 200),
        ("chairman", 200),
        ("engineer", 403),
    ],
)
def test_overview_access_matrix(client, user_factory, login, role_name, expected_status):
    if role_name:
        login(user_factory(role_name))

    response = client.get("/overview")
    assert response.status_code == expected_status
    if role_name is None:
        assert "/auth/login" in response.headers.get("Location", "")


def test_overview_contains_old_dashboard_elements(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/overview")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "لوحة التحكم العامة للدفعات" in body
    assert "paymentsDailyChart" in body
    assert "إجمالي مبالغ الدفعات حسب الحالة" in body


def test_tile_launcher_includes_overview_tile(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/dashboard")
    parser = _TileLinkParser()
    parser.feed(response.get_data(as_text=True))

    assert "/overview" in parser.hrefs


@pytest.mark.parametrize(
    "endpoint",
    [
        "/dashboard",
        "/overview",
        "/payments/",
    ],
)
def test_launcher_button_visible_on_all_pages(client, user_factory, login, endpoint):
    login(user_factory("admin"))
    response = client.get(endpoint)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/dashboard"' in body
    assert "لوحة التطبيقات" in body


def test_pages_do_not_render_sidebar(client, user_factory, login):
    login(user_factory("admin"))

    dashboard_body = client.get("/dashboard").get_data(as_text=True)
    overview_body = client.get("/overview").get_data(as_text=True)
    payments_body = client.get("/payments/").get_data(as_text=True)

    for body in (dashboard_body, overview_body, payments_body):
        assert "app-sidebar" not in body
        assert "offcanvas" not in body


def test_apps_dropdown_visible_for_authenticated_user(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/overview")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "topbar-apps-dropdown" in body
    assert "التطبيقات" in body


def test_dropdown_items_respect_roles(app_context, client, user_factory, login):
    from flask_login import login_user, logout_user
    from blueprints.main.navigation import get_launcher_modules

    admin = user_factory("admin")
    finance = user_factory("finance")

    login(admin)
    admin_body = client.get("/dashboard").get_data(as_text=True)
    assert "المستخدمون" in admin_body
    assert "لوحة الحسابات" in admin_body

    with app_context.test_request_context("/dashboard"):
        login_user(finance)
        finance_modules = get_launcher_modules(finance)
        logout_user()

    finance_titles = [module["title"] for module in finance_modules]
    assert "المستخدمون" not in finance_titles
    assert "لوحة الحسابات" in finance_titles


def test_apps_dropdown_renders_icons(client, user_factory, login):
    login(user_factory("admin"))
    response = client.get("/overview")
    parser = _DropdownIconParser()
    parser.feed(response.get_data(as_text=True))

    assert parser.icons, "Expected at least one dropdown icon to render"
    assert any("fa-" in icon for icon in parser.icons)


def test_launcher_modules_skip_missing_endpoints(app_context, monkeypatch, user_factory):
    from blueprints.main import navigation

    bad_definition = {
        "key": "missing",
        "title": "مسار مفقود",
        "description": "يجب تجاهله",
        "endpoint": "does.not.exist",
    }
    monkeypatch.setattr(
        navigation,
        "MODULE_DEFINITIONS",
        navigation.MODULE_DEFINITIONS + [bad_definition],
    )

    modules = navigation.get_launcher_modules(user_factory("admin"))
    titles = [module["title"] for module in modules]

    assert "مسار مفقود" not in titles


def test_launcher_icon_falls_back_to_default(app_context, client, user_factory, login, monkeypatch):
    from blueprints.main import navigation

    missing_icon_definition = {
        "key": "missing_icon",
        "title": "بدون أيقونة",
        "description": "يجب استخدام الأيقونة الافتراضية",
        "endpoint": "main.dashboard",
        "icon": None,
    }

    monkeypatch.setattr(
        navigation,
        "MODULE_DEFINITIONS",
        navigation.MODULE_DEFINITIONS + [missing_icon_definition],
    )

    login(user_factory("admin"))
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    assert "fa-solid fa-grid-2" in body
