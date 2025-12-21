import unittest
from datetime import datetime, timedelta

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Supplier, Role, User
from project_scopes import get_scoped_project_ids


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class EngineerMultiProjectScopeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {name: Role(name=name) for name in ["admin", "project_manager", "engineer"]}
        db.session.add_all(self.roles.values())

        self.projects = [
            Project(project_name="P1"),
            Project(project_name="P2"),
            Project(project_name="P3"),
        ]
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all(self.projects + [self.supplier])
        db.session.commit()

        self.engineer = self._create_user("eng@example.com", self.roles["engineer"], projects=self.projects[:2])

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role, *, projects=None) -> User:
        projects = projects or []
        user = User(full_name=email.split("@")[0], email=email, role=role)
        user.set_password("password")
        db.session.add(user)
        if projects:
            user.project_id = projects[0].id
            user.projects = projects
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _make_payment(self, project: Project, status: str, *, updated_at=None) -> PaymentRequest:
        payment = PaymentRequest(
            project=project,
            supplier=self.supplier,
            request_type="contractor",
            amount=100,
            status=status,
            created_by=self.engineer.id,
        )
        if updated_at:
            payment.updated_at = updated_at
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_inbox_and_dashboard_scope_across_multiple_projects(self):
        in_scope_pending = self._make_payment(self.projects[0], "pending_pm")
        in_scope_overdue = self._make_payment(
            self.projects[1],
            "pending_pm",
            updated_at=datetime.utcnow() - timedelta(days=10),
        )
        out_of_scope = self._make_payment(self.projects[2], "pending_pm")

        self._login(self.engineer)

        action_resp = self.client.get("/payments/inbox/action-required")
        self.assertEqual(action_resp.status_code, 200)
        body = action_resp.get_data(as_text=True)
        self.assertIn(f'data-payment-id="{in_scope_pending.id}"', body)
        self.assertNotIn(f'data-payment-id="{out_of_scope.id}"', body)

        overdue_resp = self.client.get("/payments/inbox/overdue")
        self.assertEqual(overdue_resp.status_code, 200)
        overdue_body = overdue_resp.get_data(as_text=True)
        self.assertIn(f'data-payment-id="{in_scope_overdue.id}"', overdue_body)
        self.assertNotIn(f'data-payment-id="{out_of_scope.id}"', overdue_body)

        scoped_ids = get_scoped_project_ids(self.engineer, role_name="engineer")
        self.assertEqual(set(scoped_ids), {self.projects[0].id, self.projects[1].id})

        detail_resp = self.client.get(f"/payments/{out_of_scope.id}")
        self.assertEqual(detail_resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
