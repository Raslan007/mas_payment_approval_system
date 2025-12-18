import unittest

from app import create_app
from config import Config
from extensions import db
from models import Role, ensure_roles


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class EnsureRolesTestCase(unittest.TestCase):
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

    def test_ensure_roles_creates_payment_notifier_when_missing(self):
        self.assertEqual(Role.query.count(), 0)

        ensure_roles()

        role_names = {role.name for role in Role.query.all()}
        self.assertIn("payment_notifier", role_names)
        self.assertEqual(Role.query.count(), len(role_names))

        # A second call should be idempotent and not create duplicates
        ensure_roles()
        self.assertEqual(Role.query.count(), len(role_names))


if __name__ == "__main__":
    unittest.main()
