import unittest
from unittest import mock

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User, user_projects
from project_scopes import get_scoped_project_ids


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "assignments-secret"
    WTF_CSRF_ENABLED = False


class ProjectAssignmentsAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()
        self.admin_client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in ["admin", "project_manager", "project_engineer", "engineer"]
        }
        db.session.add_all(self.roles.values())

        self.projects = [
            Project(project_name="Project A"),
            Project(project_name="Project B"),
        ]
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all(self.projects + [self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
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

    def _login(self, user: User, *, client=None):
        target_client = client or self.client
        with target_client.session_transaction() as sess:
            sess.clear()
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_admin_can_load_assignments_page(self):
        self._login(self.admin, client=self.admin_client)
        response = self.admin_client.get("/admin/project-assignments")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("تعيينات المشاريع", body)

    def test_non_admin_forbidden(self):
        self._login(self.engineer)
        response = self.client.get("/admin/project-assignments")
        self.assertEqual(response.status_code, 403)

    def test_post_creates_and_removes_assignments(self):
        self._login(self.admin, client=self.admin_client)

        response = self.admin_client.post(
            "/admin/project-assignments",
            data={
                "user_id": self.engineer.id,
                "scoped_role": "project_engineer",
                "project_ids": [self.projects[0].id, self.projects[1].id],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        rows = db.session.execute(user_projects.select()).all()
        self.assertEqual(len(rows), 2)

        response = self.admin_client.post(
            "/admin/project-assignments",
            data={
                "user_id": self.engineer.id,
                "scoped_role": "project_engineer",
                "project_ids": [self.projects[0].id],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        rows_after = db.session.execute(user_projects.select()).all()
        self.assertEqual(len(rows_after), 1)
        self.assertEqual(rows_after[0].project_id, self.projects[0].id)

    def test_engineer_scoping_respects_assignments(self):
        self._login(self.admin, client=self.admin_client)
        self.admin_client.post(
            "/admin/project-assignments",
            data={
                "user_id": self.engineer.id,
                "scoped_role": "project_engineer",
                "project_ids": [self.projects[0].id],
            },
            follow_redirects=True,
        )
        assignments = db.session.execute(user_projects.select()).all()
        self.assertEqual(len(assignments), 1)

        in_scope_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=100,
            status="pending_pm",
            created_by=self.engineer.id,
        )
        out_scope_payment = PaymentRequest(
            project=self.projects[1],
            supplier=self.supplier,
            request_type="contractor",
            amount=200,
            status="pending_pm",
            created_by=self.engineer.id,
        )
        db.session.add_all([in_scope_payment, out_scope_payment])
        db.session.commit()

        scoped_ids = get_scoped_project_ids(
            User.query.get(self.engineer.id),
            role_name="engineer",
        )
        self.assertEqual(set(scoped_ids), {self.projects[0].id})

        from blueprints.payments.inbox_queries import scoped_inbox_base_query, build_action_required_query
        base_q, role_name, scoped_list = scoped_inbox_base_query(self.engineer)
        self.assertEqual(role_name, "engineer")
        self.assertEqual(set(scoped_list), {self.projects[0].id})
        action_q = build_action_required_query(base_q, role_name)
        results = action_q.all()
        self.assertEqual({p.id for p in results}, {in_scope_payment.id})


if __name__ == "__main__":
    unittest.main()
