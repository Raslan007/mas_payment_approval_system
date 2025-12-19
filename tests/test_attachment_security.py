import unittest

from app import create_app
from config import Config
from extensions import db
from models import PaymentAttachment, PaymentRequest, Project, Role, Supplier, User
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class AttachmentSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        # seed roles
        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
                "engineering_manager",
                "project_manager",
                "engineer",
                "finance",
                "chairman",
                "payment_notifier",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Test Project")
        self.alt_project = Project(project_name="Alt Project")
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.alt_project, self.supplier])
        db.session.commit()

        self.users = {
            name: self._create_user(f"{name}@example.com", self.roles[name])
            for name in self.roles
        }

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role, *, project: Project | None = None) -> User:
        if project is None and role.name in ("engineer", "project_manager"):
            project = self.project

        user = User(
            full_name=email.split("@")[0],
            email=email,
            role=role,
            project_id=project.id if project else None,
        )
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _make_payment(self, *, status: str = payment_routes.STATUS_DRAFT, created_by: int | None = None):
        payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=1000.0,
            description="desc",
            status=status,
            created_by=created_by,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    def _add_attachment(self, payment: PaymentRequest) -> PaymentAttachment:
        attachment = PaymentAttachment(
            payment_request_id=payment.id,
            original_filename="file.txt",
            stored_filename="file.txt",
            mime_type="text/plain",
            uploaded_by_id=self.users["admin"].id,
        )
        db.session.add(attachment)
        db.session.commit()
        return attachment

    def test_download_blocked_for_unrelated_engineer(self):
        payment = self._make_payment(created_by=self.users["engineer"].id)
        attachment = self._add_attachment(payment)

        other_engineer = self._create_user("other_eng@example.com", self.roles["engineer"], project=self.alt_project)
        self._login(other_engineer)

        response = self.client.get(f"/payments/attachments/{attachment.id}/download")
        self.assertEqual(response.status_code, 404)

    def test_download_blocked_when_feature_disabled(self):
        self.app.config["ATTACHMENTS_ENABLED"] = False
        payment = self._make_payment(created_by=self.users["admin"].id)
        attachment = self._add_attachment(payment)

        self._login(self.users["admin"])

        response = self.client.get(f"/payments/attachments/{attachment.id}/download")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/payments/{payment.id}", response.headers.get("Location", ""))
