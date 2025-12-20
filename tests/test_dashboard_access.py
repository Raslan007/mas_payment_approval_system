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
        ("dc", 403),
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
