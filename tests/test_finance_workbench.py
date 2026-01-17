import csv
import unittest
from datetime import datetime
from decimal import Decimal
from io import StringIO

from config import Config
from app import create_app
from extensions import db
from models import PaymentFinanceAdjustment, PaymentRequest, Project, Role, Supplier, User
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class FinanceWorkbenchTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        role_names = ["admin", "finance", "engineering_manager", "engineer"]
        self.roles = {name: Role(name=name) for name in role_names}
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Alpha")
        self.supplier = Supplier(name="Acme", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.finance_user = self._create_user("finance@example.com", self.roles["finance"])
        self.engineer = self._create_user("eng@example.com", self.roles["engineer"], project=self.project)
        db.session.commit()

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
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _create_payment(self, *, status: str, amount: float = 100.0, finance_amount: float | None = None) -> PaymentRequest:
        payment = PaymentRequest(
            project_id=self.project.id,
            supplier_id=self.supplier.id,
            request_type="مقاول",
            amount=amount,
            finance_amount=finance_amount,
            status=status,
            created_by=self.admin.id,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_finance_can_access_workbench(self):
        pending_payment = self._create_payment(status=payment_routes.STATUS_PENDING_FIN)
        ready_payment = self._create_payment(status=payment_routes.STATUS_READY_FOR_PAYMENT)

        self._login(self.finance_user)
        response = self.client.get("/finance/workbench")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{pending_payment.id}</td>".encode(), response.data)
        self.assertNotIn(f"{ready_payment.id}</td>".encode(), response.data)

    def test_engineer_cannot_access_workbench(self):
        self._login(self.engineer)
        response = self.client.get("/finance/workbench")
        self.assertEqual(response.status_code, 403)

    def test_status_filter_paid_only(self):
        paid_payment = self._create_payment(status=payment_routes.STATUS_PAID, finance_amount=120)
        pending_payment = self._create_payment(status=payment_routes.STATUS_PENDING_FIN)

        self._login(self.finance_user)
        response = self.client.get("/finance/workbench", query_string={"status": payment_routes.STATUS_PAID})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{paid_payment.id}</td>".encode(), response.data)
        self.assertNotIn(f"{pending_payment.id}</td>".encode(), response.data)

    def test_export_returns_csv(self):
        payment = self._create_payment(status=payment_routes.STATUS_PENDING_FIN, finance_amount=110)
        self._login(self.finance_user)

        response = self.client.get("/finance/workbench/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)

        rows = list(csv.reader(StringIO(response.get_data(as_text=True))))
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0][:3], ["id", "project", "supplier"])
        exported_ids = [row[0] for row in rows[1:]]
        self.assertIn(str(payment.id), exported_ids)

    def test_workbench_uses_effective_finance_amounts(self):
        payment = self._create_payment(
            status=payment_routes.STATUS_PENDING_FIN,
            amount=250.0,
            finance_amount=8000,
        )
        adjustments = [
            PaymentFinanceAdjustment(
                payment_id=payment.id,
                delta_amount=Decimal("72000.00"),
                reason="Increase",
                notes="",
                created_by_user_id=self.finance_user.id,
            ),
            PaymentFinanceAdjustment(
                payment_id=payment.id,
                delta_amount=Decimal("999.00"),
                reason="Voided",
                notes="",
                created_by_user_id=self.finance_user.id,
                is_void=True,
                void_reason="Ignore",
                voided_by_user_id=self.finance_user.id,
                voided_at=datetime.utcnow(),
            ),
        ]
        db.session.add_all(adjustments)
        db.session.commit()

        self._login(self.finance_user)
        response = self.client.get("/finance/workbench")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data.count(b"80,000.00"), 2)

        export_response = self.client.get("/finance/workbench/export")
        self.assertEqual(export_response.status_code, 200)
        rows = list(csv.reader(StringIO(export_response.get_data(as_text=True))))
        payment_row = next(row for row in rows[1:] if row[0] == str(payment.id))
        self.assertEqual(payment_row[6], "80000.00")


if __name__ == "__main__":
    unittest.main()
