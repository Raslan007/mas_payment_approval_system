import unittest

from config import Config
from app import create_app
from extensions import db
from models import Role, User


class RoleRequiredTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class ChairmanAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(RoleRequiredTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in ["admin", "chairman"]
        }
        db.session.add_all(self.roles.values())
        db.session.commit()

        self.chairman_user = self._create_user("chairman@example.com", self.roles["chairman"])
        self.admin_user = self._create_user("admin@example.com", self.roles["admin"])

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

    def test_chairman_cannot_bypass_role_checks_on_get_routes(self):
        self._login(self.chairman_user)
        for path in ("/users/", "/users/create", "/projects/", "/suppliers/"):
            response = self.client.get(path)
            self.assertEqual(
                response.status_code,
                403,
                f"Expected 403 for chairman on {path}",
            )

    def test_chairman_can_access_dashboard_when_allowed(self):
        self._login(self.chairman_user)
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)

    def test_admin_still_has_full_access(self):
        self._login(self.admin_user)
        response = self.client.get("/users/")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
