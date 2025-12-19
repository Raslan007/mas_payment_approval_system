import unittest

from app import create_app
from config import Config
from extensions import db
from models import Role, SavedView, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class SavedViewsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
                "finance",
            ]
        }
        db.session.add_all(self.roles.values())
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.finance = self._create_user("finance@example.com", self.roles["finance"])

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
        return self.client.post(
            "/auth/login",
            data={"email": user.email, "password": "password"},
            follow_redirects=False,
        )

    def test_create_open_delete_saved_view_flow(self):
        self._login(self.admin)
        response = self.client.post(
            "/payments/saved_views/create",
            data={
                "name": "Drafts",
                "endpoint": "payments.index",
                "query_string": "status=draft",
                "return_to": "/payments/?status=draft",
            },
        )

        self.assertEqual(response.status_code, 302)
        saved_view = SavedView.query.filter_by(user_id=self.admin.id).first()
        self.assertIsNotNone(saved_view)
        self.assertEqual(saved_view.endpoint, "payments.index")
        self.assertEqual(saved_view.query_string, "status=draft")

        open_response = self.client.get(f"/payments/saved_views/{saved_view.id}/open")
        self.assertEqual(open_response.status_code, 302)
        self.assertIn("status=draft", open_response.headers["Location"])
        self.assertIn("/payments", open_response.headers["Location"])

        delete_response = self.client.post(
            f"/payments/saved_views/{saved_view.id}/delete",
            data={"return_to": "/payments/saved_views"},
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertEqual(SavedView.query.count(), 0)

    def test_ownership_protection(self):
        self._login(self.admin)
        response = self.client.post(
            "/payments/saved_views/create",
            data={
                "name": "Finance Ready",
                "endpoint": "payments.finance_eng_approved",
                "query_string": "per_page=20",
            },
        )
        self.assertEqual(response.status_code, 302)
        view = SavedView.query.filter_by(user_id=self.admin.id).first()
        self.assertIsNotNone(view)

        self._login(self.finance)
        open_response = self.client.get(f"/payments/saved_views/{view.id}/open")
        self.assertEqual(open_response.status_code, 404)

        delete_response = self.client.post(f"/payments/saved_views/{view.id}/delete")
        self.assertEqual(delete_response.status_code, 404)

    def test_disallowed_endpoint_rejected(self):
        self._login(self.admin)
        response = self.client.post(
            "/payments/saved_views/create",
            data={
                "name": "Login Page",
                "endpoint": "auth.login",
                "query_string": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SavedView.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
