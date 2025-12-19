import unittest
from datetime import datetime, timedelta

from app import create_app
from config import Config
from extensions import db
from blueprints.main.dashboard_helpers import compute_overdue_items, compute_stage_sla_metrics, resolve_sla_thresholds
from models import PaymentApproval, PaymentRequest, Project, Role, Supplier, User


class DashboardHelperTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class DashboardHelperTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DashboardHelperTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

        self.role = Role(name="admin")
        self.user = User(full_name="Admin", email="admin@example.com", role=self.role)
        self.user.set_password("password")
        self.project = Project(project_name="Helper Project")
        self.supplier = Supplier(name="Vendor", supplier_type="contractor")
        db.session.add_all([self.role, self.user, self.project, self.supplier])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_compute_overdue_items_respects_thresholds(self):
        base_time = datetime.utcnow()
        late_payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=1000,
            status="pending_finance",
            created_by=self.user.id,
        )
        late_payment.updated_at = base_time - timedelta(days=5)
        on_time_payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=500,
            status="pending_pm",
            created_by=self.user.id,
        )
        on_time_payment.updated_at = base_time - timedelta(days=1)
        db.session.add_all([late_payment, on_time_payment])
        db.session.commit()

        sla_thresholds = resolve_sla_thresholds({"SLA_THRESHOLDS_DAYS": {"pending_finance": 3, "pending_pm": 4}})
        result = compute_overdue_items([late_payment, on_time_payment], sla_thresholds, now=base_time)

        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["items"][0]["days_overdue"], 2)
        self.assertEqual(result["summary"]["worst_stage"], "pending_finance")

    def test_compute_stage_sla_metrics_averages_by_stage(self):
        pr = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=2000,
            status="pending_finance",
            created_by=self.user.id,
        )
        pr.created_at = datetime(2024, 1, 1)
        db.session.add(pr)
        db.session.commit()

        approval1 = PaymentApproval(
            payment_request_id=pr.id,
            step="pm",
            action="approve",
            old_status="pending_pm",
            new_status="pending_eng",
            decided_by=self.user,
            decided_at=datetime(2024, 1, 3),
        )
        approval2 = PaymentApproval(
            payment_request_id=pr.id,
            step="eng_manager",
            action="approve",
            old_status="pending_eng",
            new_status="pending_finance",
            decided_by=self.user,
            decided_at=datetime(2024, 1, 6),
        )
        db.session.add_all([approval1, approval2])
        db.session.commit()

        metrics = compute_stage_sla_metrics([pr.id])
        lookup = {m["stage"]: m for m in metrics}

        self.assertIn("pending_pm", lookup)
        self.assertIn("pending_eng", lookup)
        self.assertAlmostEqual(lookup["pending_pm"]["average_days"], 2.0)
        self.assertAlmostEqual(lookup["pending_eng"]["average_days"], 3.0)


if __name__ == "__main__":
    unittest.main()
