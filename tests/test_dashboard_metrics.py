import unittest
from datetime import datetime, timedelta

from flask import url_for
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

        now = datetime.utcnow()
        self.pending_finance = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=5000,
            status="pending_finance",
            created_by=self.admin_user.id,
        )
        self.pending_finance.updated_at = now - timedelta(days=10)
        self.pending_eng = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=2500,
            status="pending_eng",
            created_by=self.admin_user.id,
        )
        self.pending_eng.updated_at = now - timedelta(days=2)
        self.ready_for_payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=3000,
            status="ready_for_payment",
            created_by=self.admin_user.id,
        )
        self.ready_for_payment.updated_at = now - timedelta(days=1)
        self.paid_this_month = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=7000,
            amount_finance=6500,
            status="paid",
            created_by=self.admin_user.id,
        )
        self.paid_this_month.updated_at = now - timedelta(days=3)
        db.session.add_all(
            [self.pending_finance, self.pending_eng, self.ready_for_payment, self.paid_this_month]
        )
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
        self.assertIn(f'data-ready-payment-id="{self.ready_for_payment.id}"', body)

    def test_financial_and_operational_kpis_render(self):
        self._login(self.finance_user)
        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-kpi="outstanding_amount" data-kpi-value="10500.0"', body)
        self.assertIn('data-kpi="paid_this_month" data-kpi-value="6500.0"', body)
        self.assertIn('data-kpi="action_required" data-kpi-value="2"', body)
        self.assertIn('data-overdue-stage="pending_finance"', body)

    def test_dashboard_charts_include_datasets_and_listing_link(self):
        self._login(self.finance_user)
        with self.app.test_request_context():
            listing_url = url_for("payments.index")

        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="workflowFunnelChart"', body)
        self.assertIn('id="cashFlowChart"', body)
        self.assertIn("workflowFunnel", body)
        self.assertIn("ready_for_payment", body)
        self.assertIn(listing_url, body)


if __name__ == "__main__":
    unittest.main()
