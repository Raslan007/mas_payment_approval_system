import re
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import create_app
from config import Config
from extensions import db
from models import (
    Role,
    User,
    Supplier,
    Project,
    SupplierLedgerEntry,
    PurchaseOrder,
    PaymentRequest,
    PURCHASE_ORDER_STATUS_SUBMITTED,
)


class SupplierLedgerConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app_context():
    app = create_app(SupplierLedgerConfig)
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


@pytest.fixture()
def supplier(app_context):
    supplier = Supplier(name="Legacy Supplier", supplier_type="مورد")
    db.session.add(supplier)
    db.session.commit()
    return supplier


@pytest.fixture()
def project(app_context):
    project = Project(project_name="Legacy Project", code="LP-1")
    db.session.add(project)
    db.session.commit()
    return project


def test_finance_can_create_opening_balance(client, user_factory, login, supplier):
    finance_user = user_factory("finance")
    login(finance_user)

    response = client.post(
        f"/suppliers/{supplier.id}/ledger/opening-balance",
        data={
            "amount": "1200.00",
            "entry_date": "2024-01-10",
            "note": "Opening balance",
        },
    )

    assert response.status_code == 302
    entry = SupplierLedgerEntry.query.one()
    assert entry.entry_type == "opening_balance"
    assert entry.direction == "debit"
    assert entry.amount == Decimal("1200.00")


def test_engineer_cannot_create_opening_balance(client, user_factory, login, supplier):
    engineer = user_factory("engineer")
    login(engineer)

    response = client.post(
        f"/suppliers/{supplier.id}/ledger/opening-balance",
        data={
            "amount": "500.00",
            "entry_date": "2024-01-10",
        },
    )

    assert response.status_code == 403
    assert SupplierLedgerEntry.query.count() == 0


@pytest.mark.parametrize(
    "role_name",
    ["accounts", "procurement", "engineering_manager", "chairman"],
)
def test_viewer_roles_cannot_create_opening_balance(client, user_factory, login, supplier, role_name):
    user = user_factory(role_name)
    login(user)

    response = client.post(
        f"/suppliers/{supplier.id}/ledger/opening-balance",
        data={
            "amount": "500.00",
            "entry_date": "2024-01-10",
        },
    )

    assert response.status_code == 403
    assert SupplierLedgerEntry.query.count() == 0


def test_voiding_excludes_entry_from_legacy_balance(client, user_factory, login, supplier):
    finance_user = user_factory("finance")
    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        entry_type="adjustment",
        direction="debit",
        amount=Decimal("250.00"),
        entry_date=date(2024, 1, 5),
        created_by_id=finance_user.id,
    )
    db.session.add(entry)
    db.session.commit()

    login(finance_user)
    response = client.post(f"/suppliers/{supplier.id}/ledger/{entry.id}/void")
    assert response.status_code == 302

    db.session.refresh(entry)
    db.session.refresh(supplier)
    assert entry.voided_at is not None
    assert supplier.legacy_balance == Decimal("0.00")


def test_legacy_balance_calculates_debits_minus_credits(supplier, user_factory):
    finance_user = user_factory("finance")
    entries = [
        SupplierLedgerEntry(
            supplier_id=supplier.id,
            entry_type="opening_balance",
            direction="debit",
            amount=Decimal("100.00"),
            entry_date=date(2024, 1, 1),
            created_by_id=finance_user.id,
        ),
        SupplierLedgerEntry(
            supplier_id=supplier.id,
            entry_type="adjustment",
            direction="credit",
            amount=Decimal("40.00"),
            entry_date=date(2024, 1, 2),
            created_by_id=finance_user.id,
        ),
        SupplierLedgerEntry(
            supplier_id=supplier.id,
            entry_type="adjustment",
            direction="debit",
            amount=Decimal("20.00"),
            entry_date=date(2024, 1, 3),
            created_by_id=finance_user.id,
            voided_at=datetime(2024, 1, 4, 0, 0, 0),
        ),
    ]
    db.session.add_all(entries)
    db.session.commit()

    assert supplier.legacy_balance == Decimal("60.00")


@pytest.mark.parametrize(
    "role_name",
    ["admin", "engineering_manager", "procurement", "accounts", "chairman", "finance"],
)
def test_overview_includes_legacy_liabilities_metric(client, user_factory, login, supplier, role_name):
    user = user_factory(role_name)
    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        entry_type="opening_balance",
        direction="debit",
        amount=Decimal("150.00"),
        entry_date=date(2024, 1, 1),
        created_by_id=user.id,
    )
    db.session.add(entry)
    db.session.commit()

    login(user)
    response = client.get("/overview")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Legacy Liabilities (Pre-system)" in body
    assert "150.00" in body


def _extract_commitments_total(html: str) -> Decimal:
    match = re.search(
        r"إجمالي الالتزامات.*?<div class=\"h4 mb-0\">\s*([^<]+)</div>",
        html,
        re.S,
    )
    assert match, "Expected commitments total to render"
    value = match.group(1).strip().replace(",", "")
    return Decimal(value)


def test_commitments_totals_unchanged_by_ledger_entries(client, user_factory, login, supplier, project):
    admin = user_factory("admin")
    purchase_order = PurchaseOrder(
        bo_number="BO-100",
        project_id=project.id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        total_amount=Decimal("1000.00"),
        advance_amount=Decimal("0.00"),
        reserved_amount=Decimal("0.00"),
        paid_amount=Decimal("0.00"),
        remaining_amount=Decimal("1000.00"),
        status=PURCHASE_ORDER_STATUS_SUBMITTED,
        created_by_id=admin.id,
    )
    db.session.add(purchase_order)
    db.session.commit()

    login(admin)
    response = client.get("/eng-dashboard/commitments")
    assert response.status_code == 200
    before_total = _extract_commitments_total(response.get_data(as_text=True))

    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        entry_type="opening_balance",
        direction="debit",
        amount=Decimal("500.00"),
        entry_date=date(2024, 1, 10),
        created_by_id=admin.id,
    )
    db.session.add(entry)
    db.session.commit()

    response_after = client.get("/eng-dashboard/commitments")
    after_total = _extract_commitments_total(response_after.get_data(as_text=True))

    assert before_total == after_total


def test_supplier_delete_blocked_when_payments_exist(client, user_factory, login, supplier, project):
    admin = user_factory("admin")
    payment = PaymentRequest(
        project_id=project.id,
        supplier_id=supplier.id,
        request_type="مشتريات",
        amount=Decimal("200.00"),
        created_by=admin.id,
    )
    db.session.add(payment)
    db.session.commit()

    login(admin)
    response = client.post(f"/suppliers/{supplier.id}/delete")
    assert response.status_code == 302
    assert Supplier.query.get(supplier.id) is not None


def test_supplier_delete_blocked_when_ledger_entries_exist(client, user_factory, login, supplier):
    admin = user_factory("admin")
    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        entry_type="opening_balance",
        direction="debit",
        amount=Decimal("200.00"),
        entry_date=date(2024, 2, 1),
        created_by_id=admin.id,
    )
    db.session.add(entry)
    db.session.commit()

    login(admin)
    response = client.post(f"/suppliers/{supplier.id}/delete")
    assert response.status_code == 302
    assert Supplier.query.get(supplier.id) is not None


def test_supplier_delete_allowed_without_payments_or_ledger(client, user_factory, login):
    admin = user_factory("admin")
    supplier = Supplier(name="Delete Me", supplier_type="مورد")
    db.session.add(supplier)
    db.session.commit()

    login(admin)
    response = client.post(f"/suppliers/{supplier.id}/delete")
    assert response.status_code == 302
    assert Supplier.query.get(supplier.id) is None


@pytest.mark.parametrize(
    ("role_name", "expected_status"),
    [
        ("admin", 200),
        ("engineering_manager", 200),
        ("procurement", 200),
        ("accounts", 200),
        ("chairman", 200),
        ("finance", 200),
        ("dc", 403),
    ],
)
def test_ledger_view_permissions(client, user_factory, login, supplier, role_name, expected_status):
    user = user_factory(role_name)
    login(user)

    response = client.get(f"/suppliers/{supplier.id}/ledger")
    assert response.status_code == expected_status


def test_dc_cannot_view_legacy_liabilities_kpi(client, user_factory, login, supplier):
    dc_user = user_factory("dc")
    entry = SupplierLedgerEntry(
        supplier_id=supplier.id,
        entry_type="opening_balance",
        direction="debit",
        amount=Decimal("200.00"),
        entry_date=date(2024, 1, 1),
        created_by_id=dc_user.id,
    )
    db.session.add(entry)
    db.session.commit()

    login(dc_user)
    response = client.get("/overview")
    assert response.status_code == 403
