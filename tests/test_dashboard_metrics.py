import unittest

from config import Config
from app import create_app
from extensions import db
from models import Notification, Role, User


class DashboardUITestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class DashboardUITestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DashboardUITestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {name: Role(name=name) for name in ["admin", "finance", "dc"]}
        db.session.add_all(self.roles.values())
        db.session.commit()

        self.admin_user = self._create_user("admin@example.com", self.roles["admin"])
        self.finance_user = self._create_user("finance@example.com", self.roles["finance"])

        notification = Notification(user=self.admin_user, title="Test", message="Hello")
        db.session.add(notification)
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

    def test_dashboard_loads_tiles_and_topbar(self):
        self._login(self.admin_user)
        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("dashboard-topbar", body)
        self.assertIn("tile-grid", body)
        self.assertIn("الإشعارات", body)
        self.assertIn("الدفعات", body)
        self.assertIn("tile-badge", body)

    def test_tiles_are_filtered_by_role(self):
        self._login(self.finance_user)
        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("جاهزة للصرف", body)
        self.assertNotIn("المستخدمون", body)


if __name__ == "__main__":
    unittest.main()
