import csv
import unittest
from io import StringIO

from config import Config
from app import create_app
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PaymentExportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {name: Role(name=name) for name in ["admin", "project_manager", "engineer"]}
        db.session.add_all(self.roles.values())

        self.projects = [Project(project_name="Alpha"), Project(project_name="Beta")]
        self.supplier = Supplier(name="Acme", supplier_type="contractor")
        db.session.add_all([*self.projects, self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.pm = self._create_user("pm@example.com", self.roles["project_manager"], project=self.projects[0])
        # ensure pm linked to project via association table
        self.pm.projects.append(self.projects[0])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role, project: Project | None = None) -> User:
        user = User(full_name=email.split("@")[0], email=email, role=role)
        user.set_password("password")
        if project:
            user.project = project
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_export_all_respects_status_filter(self):
        pending_pm = PaymentRequest(
            project_id=self.projects[0].id,
            supplier_id=self.supplier.id,
            request_type="contractor",
            amount=100,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        pending_eng = PaymentRequest(
            project_id=self.projects[0].id,
            supplier_id=self.supplier.id,
            request_type="contractor",
            amount=120,
            status=payment_routes.STATUS_PENDING_ENG,
            created_by=self.admin.id,
        )
        db.session.add_all([pending_pm, pending_eng])
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(f"/payments/all/export?status={payment_routes.STATUS_PENDING_PM}")
        self.assertEqual(response.status_code, 200)

        rows = list(csv.reader(StringIO(response.get_data(as_text=True))))
        exported_ids = [row[0] for row in rows[1:]]
        self.assertIn(str(pending_pm.id), exported_ids)
        self.assertNotIn(str(pending_eng.id), exported_ids)

    def test_project_manager_export_respects_scope(self):
        pm_payment = PaymentRequest(
            project_id=self.projects[0].id,
            supplier_id=self.supplier.id,
            request_type="contractor",
            amount=75,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.pm.id,
        )
        other_payment = PaymentRequest(
            project_id=self.projects[1].id,
            supplier_id=self.supplier.id,
            request_type="contractor",
            amount=50,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        db.session.add_all([pm_payment, other_payment])
        db.session.commit()

        self._login(self.pm)
        response = self.client.get("/payments/export")
        self.assertEqual(response.status_code, 200)

        rows = list(csv.reader(StringIO(response.get_data(as_text=True))))
        exported_ids = [row[0] for row in rows[1:]]
        self.assertIn(str(pm_payment.id), exported_ids)
        self.assertNotIn(str(other_payment.id), exported_ids)


if __name__ == "__main__":
    unittest.main()
