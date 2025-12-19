import unittest

from config import Config
from app import create_app
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User


class DashboardTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class DashboardMetricsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DashboardTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in ["admin", "finance"]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Test Project")
        self.supplier = Supplier(name="ACME", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.finance_user = self._create_user("finance@example.com", self.roles["finance"])
        self.admin_user = self._create_user("admin@example.com", self.roles["admin"])

        self.pending_finance = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=5000,
            status="pending_finance",
            created_by=self.admin_user.id,
        )
        self.pending_eng = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=2500,
            status="pending_eng",
            created_by=self.admin_user.id,
        )
        db.session.add_all([self.pending_finance, self.pending_eng])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role) -> User:
        user = User(full_name=email.split("@")[0], email=email, role=role)
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_finance_action_required_only_shows_finance_steps(self):
        self._login(self.finance_user)
        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'data-payment-id="{self.pending_finance.id}"', body)
        self.assertNotIn(f'data-payment-id="{self.pending_eng.id}"', body)
        self.assertIn('data-kpi="pending_review"', body)


if __name__ == "__main__":
    unittest.main()
