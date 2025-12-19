import re
import unittest

from app import create_app
from config import Config
from extensions import db
from models import User


class CsrfTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = True


class CsrfProtectionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(CsrfTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.user = User(full_name="Test User", email="user@example.com")
        self.user.set_password("password")
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _extract_csrf_token(self) -> str:
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        match = re.search(r'name=\"csrf_token\" value=\"([^\"]+)\"', body)
        self.assertIsNotNone(match, "CSRF token should be rendered on the login form")
        return match.group(1)

    def test_login_without_csrf_token_is_rejected(self):
        response = self.client.post(
            "/auth/login",
            data={"email": self.user.email, "password": "password"},
        )
        self.assertEqual(response.status_code, 400)

    def test_login_with_csrf_token_succeeds(self):
        csrf_token = self._extract_csrf_token()
        response = self.client.post(
            "/auth/login",
            data={
                "email": self.user.email,
                "password": "password",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers.get("Location", "").endswith("/"),
            "Successful login should redirect to the home page",
        )


if __name__ == "__main__":
    unittest.main()
