import unittest

from app import create_app
from config import Config
from extensions import db
from models import Notification, Role, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class NotificationSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.role = Role(name="admin")
        db.session.add(self.role)
        db.session.commit()

        self.user = self._create_user("user1@example.com")
        self.other_user = self._create_user("user2@example.com")

        self.other_notification = Notification(
            user_id=self.other_user.id,
            title="Test notification",
            message="Only visible to other user",
        )
        db.session.add(self.other_notification)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str) -> User:
        user = User(full_name=email.split("@")[0], email=email, role=self.role)
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_user_cannot_mark_other_user_notification(self):
        self._login(self.user)

        response = self.client.post(f"/notifications/{self.other_notification.id}/read")

        self.assertEqual(response.status_code, 403)
        reloaded = db.session.get(Notification, self.other_notification.id)
        self.assertFalse(reloaded.is_read)

    def test_list_shows_only_current_user_notifications(self):
        my_notification = Notification(
            user_id=self.user.id,
            title="My notification",
            message="Visible to current user",
        )
        db.session.add(my_notification)
        db.session.commit()

        self._login(self.user)
        response = self.client.get("/notifications/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My notification", response.data)
        self.assertNotIn(b"Only visible to other user", response.data)
