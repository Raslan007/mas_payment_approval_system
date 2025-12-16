import unittest

from sqlalchemy import text

from app import create_app
from config import Config
from extensions import db
from models import Project, Role, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class UserEditViewMissingUserProjectsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        self.role_admin = Role(name="admin")
        self.project = Project(project_name="Demo Project")
        db.session.add_all([self.role_admin, self.project])
        db.session.commit()

        self.admin = User(
            full_name="Admin",
            email="admin@example.com",
            role=self.role_admin,
        )
        self.admin.set_password("password")
        db.session.add(self.admin)
        db.session.commit()

        # Simulate environments where the association table was not created yet.
        db.session.execute(text("DROP TABLE IF EXISTS user_projects"))
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_edit_page_renders_without_user_projects_table(self):
        self._login(self.admin)

        response = self.client.get(f"/users/{self.admin.id}/edit")

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
