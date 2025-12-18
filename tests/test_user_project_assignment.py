import unittest

from app import create_app
from config import Config
from extensions import db
from models import Role, User, Project
from blueprints.users import routes as user_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class UserProjectAssignmentTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in ["admin", "project_manager", "engineer"]
        }
        db.session.add_all(self.roles.values())

        self.projects = [
            Project(project_name="Alpha Project"),
            Project(project_name="Beta Project"),
        ]
        db.session.add_all(self.projects)
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.pm = self._create_user("pm@example.com", self.roles["project_manager"])
        self.engineer = self._create_user("eng@example.com", self.roles["engineer"])

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

    def test_admin_can_view_assignment_page(self):
        self._login(self.admin)
        response = self.client.get(f"/users/{self.pm.id}/projects")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("إسناد مشاريع للمستخدم", body)
        self.assertIn("Alpha Project", body)

    def test_admin_can_update_assignments(self):
        self._login(self.admin)
        response = self.client.post(
            f"/users/{self.pm.id}/projects",
            data={"project_ids": [str(self.projects[0].id), str(self.projects[1].id)]},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.pm)
        project_ids = {p.id for p in self.pm.projects}
        self.assertEqual(project_ids, {self.projects[0].id, self.projects[1].id})

    def test_non_admin_gets_forbidden(self):
        self._login(self.pm)
        response = self.client.get(f"/users/{self.admin.id}/projects")
        self.assertEqual(response.status_code, 403)

    def test_missing_user_projects_table_is_handled(self):
        original_check = user_routes._user_projects_table_exists
        user_routes._user_projects_table_exists = lambda: False
        try:
            self._login(self.admin)

            response = self.client.get(f"/users/{self.pm.id}/projects")
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn("جدول ربط المستخدمين بالمشاريع غير متوفر", body)
        finally:
            user_routes._user_projects_table_exists = original_check


if __name__ == "__main__":
    unittest.main()
