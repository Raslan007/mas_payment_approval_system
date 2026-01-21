import html
import re
import unittest
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from app import create_app
from config import Config
from extensions import db
from models import (
    Project,
    PurchaseOrder,
    Role,
    Supplier,
    User,
    PURCHASE_ORDER_STATUS_DRAFT,
)


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PurchaseOrderReturnToTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.role = Role(name="procurement")
        db.session.add(self.role)
        self.project = Project(project_name="Alpha")
        self.supplier = Supplier(name="Vendor", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.user = self._create_user("procurement@example.com", self.role, self.project)
        purchase_orders = []
        for idx in range(21):
            purchase_orders.append(
                PurchaseOrder(
                    bo_number=f"BO0-{idx}",
                    project_id=self.project.id,
                    supplier_id=self.supplier.id,
                    supplier_name=self.supplier.name,
                    total_amount=Decimal("100.00"),
                    advance_amount=Decimal("10.00"),
                    remaining_amount=Decimal("90.00"),
                    status=PURCHASE_ORDER_STATUS_DRAFT,
                    created_by_id=self.user.id,
                )
            )
        db.session.add_all(purchase_orders)
        db.session.commit()
        self.purchase_order = purchase_orders[0]

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role, project: Project | None = None) -> User:
        user = User(full_name=email.split("@")[0], email=email, role=role)
        user.set_password("password")
        if project:
            user.project = project
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_purchase_order_return_to_flow(self):
        self._login(self.user)
        list_path = (
            f"/purchase-orders/?project_id={self.project.id}"
            "&status=draft&bo_number=BO0&supplier_name=Vendor&page=2&per_page=20"
        )
        response = self.client.get(list_path)
        body = html.unescape(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)

        match = re.search(rf'href="(?P<href>/purchase-orders/{self.purchase_order.id}\?[^\"]+)"', body)
        self.assertIsNotNone(match)
        href = match.group("href")
        parsed = urlparse(href)
        return_to = parse_qs(parsed.query).get("return_to", [None])[0]
        self.assertEqual(return_to, list_path)

        detail_response = self.client.get(
            f"/purchase-orders/{self.purchase_order.id}",
            query_string={"return_to": list_path},
        )
        detail_body = html.unescape(detail_response.get_data(as_text=True))

        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(f'href="{list_path}"', detail_body)
