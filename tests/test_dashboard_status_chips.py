from datetime import datetime, timedelta

import pytest

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User


class DashboardChipsConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "chips-secret"
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app_context():
    app = create_app(DashboardChipsConfig)
    ctx = app.app_context()
    ctx.push()
    from blueprints.main import dashboard_metrics

    dashboard_metrics._STATUS_CACHE.clear()
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
        "finance",
        "project_manager",
    ]
    role_objects = {name: Role(name=name) for name in role_names}
    db.session.add_all(role_objects.values())
    db.session.commit()
    return role_objects


@pytest.fixture()
def project():
    proj = Project(project_name="Test Project", code="TP1")
    db.session.add(proj)
    db.session.commit()
    return proj


@pytest.fixture()
def supplier():
    sup = Supplier(name="ACME", supplier_type="مقاول")
    db.session.add(sup)
    db.session.commit()
    return sup


@pytest.fixture()
def login(client):
    def _login(user: User):
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    return _login


@pytest.fixture()
def user_factory(roles, project):
    def _create_user(role_name: str, *, assign_project: bool = False) -> User:
        role = roles[role_name]
        user = User(full_name=role_name, email=f"{role_name}@example.com", role=role)
        if assign_project:
            user.project = project
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    return _create_user


def _create_payment(
    *,
    status: str,
    project: Project,
    supplier: Supplier,
    creator: User | None = None,
    updated_at: datetime | None = None,
):
    payment = PaymentRequest(
        project=project,
        supplier=supplier,
        request_type="مقاول",
        amount=1000.0,
        status=status,
        creator=creator,
        created_by=creator.id if creator else None,
    )
    if updated_at:
        payment.created_at = updated_at
        payment.updated_at = updated_at
    db.session.add(payment)
    db.session.commit()
    return payment


def test_dashboard_shows_chip_counts_for_seeded_payments(client, user_factory, login, project, supplier):
    admin = user_factory("admin")
    overdue_date = datetime.utcnow() - timedelta(days=5)
    _create_payment(status="pending_pm", project=project, supplier=supplier, creator=admin)
    _create_payment(status="pending_finance", project=project, supplier=supplier, creator=admin, updated_at=overdue_date)
    _create_payment(status="ready_for_payment", project=project, supplier=supplier, creator=admin)

    login(admin)
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "مطلوب إجراء منك" in body
    assert 'chip-count">3<' in body
    assert "متأخر" in body
    assert 'chip-count">1<' in body
    assert "جاهز للصرف" in body


def test_ready_chip_hidden_for_project_managers(client, user_factory, login, project, supplier):
    pm = user_factory("project_manager", assign_project=True)
    _create_payment(status="ready_for_payment", project=project, supplier=supplier, creator=pm)
    _create_payment(status="pending_pm", project=project, supplier=supplier, creator=pm)

    login(pm)
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "جاهز للصرف" not in body
    assert "مطلوب إجراء منك" in body
    assert 'chip-count">1<' in body


def test_chips_skip_missing_endpoints(client, user_factory, login, project, supplier, monkeypatch):
    from blueprints.main import dashboard_metrics

    finance_user = user_factory("finance")
    _create_payment(status="ready_for_payment", project=project, supplier=supplier, creator=finance_user)

    monkeypatch.setattr(dashboard_metrics, "READY_ENDPOINT", "missing.endpoint", raising=False)

    login(finance_user)
    response = client.get("/dashboard")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "جاهز للصرف" in body
    assert "/missing.endpoint" not in body


def test_metrics_cached_per_user_and_ttl(client, user_factory, login, project, supplier, monkeypatch):
    from blueprints.main import dashboard_metrics

    dashboard_metrics._STATUS_CACHE.clear()
    fake_time = [1_000_000.0]

    def _fake_time():
        return fake_time[0]

    monkeypatch.setattr(dashboard_metrics.time, "time", _fake_time)

    admin = user_factory("admin")
    _create_payment(status="pending_pm", project=project, supplier=supplier, creator=admin)

    login(admin)
    first = client.get("/dashboard").get_data(as_text=True)
    assert 'chip-count">1<' in first

    _create_payment(status="pending_pm", project=project, supplier=supplier, creator=admin)
    second = client.get("/dashboard").get_data(as_text=True)
    assert 'chip-count">1<' in second  # cached result

    fake_time[0] += 31  # expire cache
    third = client.get("/dashboard").get_data(as_text=True)
    assert 'chip-count">2<' in third
