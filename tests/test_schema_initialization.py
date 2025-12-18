import unittest

from sqlalchemy import inspect

from app import create_app
from config import Config
from extensions import db
from models import PaymentNotificationNote, ensure_schema


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class EnsureSchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_ensure_schema_creates_missing_payment_notification_notes_table(self):
        # Drop the table to simulate an environment where it was not created yet.
        PaymentNotificationNote.__table__.drop(db.engine, checkfirst=True)

        inspector = inspect(db.engine)
        self.assertFalse(inspector.has_table("payment_notification_notes"))

        ensure_schema()

        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table("payment_notification_notes"))


class AutoSchemaBootstrapTestCase(unittest.TestCase):
    class AutoBootstrapConfig(TestConfig):
        AUTO_SCHEMA_BOOTSTRAP = True

    def setUp(self):
        self.app = create_app(self.AutoBootstrapConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        db.session.remove()
        db.Model.metadata.drop_all(bind=db.engine, checkfirst=True)
        self.app_context.pop()

    def test_create_app_runs_schema_bootstrap_when_enabled(self):
        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table("payment_notification_notes"))


if __name__ == "__main__":
    unittest.main()
