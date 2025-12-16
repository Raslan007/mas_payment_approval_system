import unittest

from sqlalchemy import inspect

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Supplier, Role, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PaymentsMissingUserProjectsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        inspector = inspect(db.engine)
        if inspector.has_table("user_projects"):
            db.metadata.tables["user_projects"].drop(db.engine)

        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in ["project_manager", "engineering_manager", "admin"]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Main Project")
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.pm_user = self._create_user(
            "pm@example.com", self.roles["project_manager"], self.project
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role, project: Project) -> User:
        user = User(
            full_name=email.split("@")[0],
            email=email,
            role=role,
            project_id=project.id,
        )
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def test_payments_my_handles_missing_user_projects(self):
        payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=500.0,
            description="desc",
            status="pending_pm",
            created_by=self.pm_user.id,
        )
        db.session.add(payment)
        db.session.commit()

        self._login(self.pm_user)

        response = self.client.get("/payments/my")

        self.assertNotEqual(response.status_code, 500)
        self.assertIn(str(payment.id), response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
