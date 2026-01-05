import unittest
from datetime import datetime, timedelta

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Supplier


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PurgeOldPaymentsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.runner = self.app.test_cli_runner()

        self.project = Project(project_name="Test Project")
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _make_payment(
        self,
        created_at: datetime,
        submitted_to_pm_at: datetime | None,
    ) -> PaymentRequest:
        payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=1000.0,
            description="desc",
            status="draft",
            created_at=created_at,
            submitted_to_pm_at=submitted_to_pm_at,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_purge_removes_old_pm_date_records(self):
        now = datetime.utcnow()
        old = now - timedelta(days=15)
        recent = now - timedelta(days=5)

        submitted_old = self._make_payment(created_at=recent, submitted_to_pm_at=old)
        created_old = self._make_payment(created_at=old, submitted_to_pm_at=None)
        fresh = self._make_payment(created_at=recent, submitted_to_pm_at=None)
        submitted_old_id = submitted_old.id
        created_old_id = created_old.id
        fresh_id = fresh.id

        result = self.runner.invoke(self.app.cli, ["purge-old-payments", "--days", "14"])
        self.assertEqual(result.exit_code, 0)

        self.assertIsNone(db.session.get(PaymentRequest, submitted_old_id))
        self.assertIsNone(db.session.get(PaymentRequest, created_old_id))
        self.assertIsNotNone(db.session.get(PaymentRequest, fresh_id))

    def test_dry_run_leaves_records_intact(self):
        now = datetime.utcnow()
        old = now - timedelta(days=20)
        payment = self._make_payment(created_at=old, submitted_to_pm_at=None)

        result = self.runner.invoke(
            self.app.cli, ["purge-old-payments", "--days", "14", "--dry-run"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIsNotNone(db.session.get(PaymentRequest, payment.id))
