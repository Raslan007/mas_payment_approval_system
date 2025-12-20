import csv
import unittest
from io import StringIO

from config import Config
from app import create_app
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User
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

    def _create_payment(self, *, status: str, amount: float = 100.0, amount_finance: float | None = None) -> PaymentRequest:
        payment = PaymentRequest(
            project_id=self.project.id,
            supplier_id=self.supplier.id,
            request_type="مقاول",
            amount=amount,
            amount_finance=amount_finance,
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
        paid_payment = self._create_payment(status=payment_routes.STATUS_PAID, amount_finance=120)
        pending_payment = self._create_payment(status=payment_routes.STATUS_PENDING_FIN)

        self._login(self.finance_user)
        response = self.client.get("/finance/workbench", query_string={"status": payment_routes.STATUS_PAID})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{paid_payment.id}</td>".encode(), response.data)
        self.assertNotIn(f"{pending_payment.id}</td>".encode(), response.data)

    def test_export_returns_csv(self):
        payment = self._create_payment(status=payment_routes.STATUS_PENDING_FIN, amount_finance=110)
        self._login(self.finance_user)

        response = self.client.get("/finance/workbench/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)

        rows = list(csv.reader(StringIO(response.get_data(as_text=True))))
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0][:3], ["id", "project", "supplier"])
        exported_ids = [row[0] for row in rows[1:]]
        self.assertIn(str(payment.id), exported_ids)


if __name__ == "__main__":
    unittest.main()
