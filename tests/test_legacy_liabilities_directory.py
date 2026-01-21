from datetime import date
from decimal import Decimal

import pytest

from app import create_app
from config import Config
from extensions import db
from models import Role, Supplier, SupplierLedgerEntry, User


class LegacyLiabilitiesConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app_context():
    app = create_app(LegacyLiabilitiesConfig)
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
        "accounts",
        "procurement",
        "chairman",
    ]
    role_objects = {name: Role(name=name) for name in role_names}
    db.session.add_all(role_objects.values())
    db.session.commit()
    return role_objects


@pytest.fixture()
def user_factory(roles):
    counter = {"value": 0}

    def _create_user(role_name: str) -> User:
        counter["value"] += 1
        role = roles[role_name]
        user = User(
            full_name=role_name,
            email=f"{role_name}-{counter['value']}@example.com",
            role=role,
        )
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


@pytest.fixture()
def suppliers(user_factory):
    creator = user_factory("admin")
    supplier_a = Supplier(name="Alpha Supplier", supplier_type="مورد مواد")
    supplier_b = Supplier(name="Beta Supplier", supplier_type="مقاول")
    db.session.add_all([supplier_a, supplier_b])
    db.session.flush()

    entries = [
        SupplierLedgerEntry(
            supplier_id=supplier_a.id,
            entry_type="opening_balance",
            direction="debit",
            amount=Decimal("100.00"),
            entry_date=date(2024, 1, 1),
            created_by_id=creator.id,
        ),
        SupplierLedgerEntry(
            supplier_id=supplier_b.id,
            entry_type="opening_balance",
            direction="debit",
            amount=Decimal("50.00"),
            entry_date=date(2024, 1, 2),
            created_by_id=creator.id,
        ),
    ]
    db.session.add_all(entries)
    db.session.commit()
    return supplier_a, supplier_b


def test_procurement_can_view_directory(client, user_factory, login, suppliers):
    procurement = user_factory("procurement")
    login(procurement)

    response = client.get("/finance/suppliers")

    assert response.status_code == 200


def test_directory_shows_suppliers_and_ledger_links(client, user_factory, login, suppliers):
    procurement = user_factory("procurement")
    login(procurement)

    response = client.get("/finance/suppliers")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    for supplier in suppliers:
        assert supplier.name in body
        assert f"/suppliers/{supplier.id}/ledger" in body


def test_directory_hides_supplier_admin_links(client, user_factory, login, suppliers):
    procurement = user_factory("procurement")
    login(procurement)

    response = client.get("/finance/suppliers")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/suppliers/list" not in body
    for supplier in suppliers:
        assert f"/suppliers/{supplier.id}/edit" not in body
        assert f"/suppliers/{supplier.id}/delete" not in body


def test_dc_is_denied_directory(client, user_factory, login, suppliers):
    dc_user = user_factory("dc")
    login(dc_user)

    response = client.get("/finance/suppliers")

    assert response.status_code == 403


def test_admin_can_view_directory(client, user_factory, login, suppliers):
    admin = user_factory("admin")
    login(admin)

    response = client.get("/finance/suppliers")

    assert response.status_code == 200
