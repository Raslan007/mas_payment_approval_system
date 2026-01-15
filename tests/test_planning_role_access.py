import unittest

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User
from blueprints.payments import routes as payment_routes


class PlanningAccessConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PlanningRoleAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PlanningAccessConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
                "planning",
                "engineering_manager",
                "project_manager",
                "engineer",
                "finance",
                "chairman",
                "payment_notifier",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Planning Project")
        self.supplier = Supplier(name="Planning Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.planning_user = self._create_user("planning@example.com", self.roles["planning"])

        self.payment = self._make_payment(
            status=payment_routes.STATUS_DRAFT,
            created_by=self.admin.id,
        )

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

    def _make_payment(self, status: str, created_by: int | None = None) -> PaymentRequest:
        payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=1200.0,
            description="planning",
            status=status,
            created_by=created_by,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_planning_can_view_payments_list_and_detail(self):
        self._login(self.planning_user)

        list_response = self.client.get("/payments/")
        self.assertEqual(list_response.status_code, 200)

        detail_response = self.client.get(f"/payments/{self.payment.id}")
        self.assertEqual(detail_response.status_code, 200)

    def test_planning_can_view_dashboard(self):
        self._login(self.planning_user)

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)

    def test_planning_cannot_mutate_payments(self):
        self._login(self.planning_user)

        endpoints = [
            ("/payments/create", {}),
            (f"/payments/{self.payment.id}/edit", {}),
            (f"/payments/{self.payment.id}/delete", {}),
            (f"/payments/{self.payment.id}/submit_to_pm", {}),
            (f"/payments/{self.payment.id}/pm_approve", {}),
            (f"/payments/{self.payment.id}/pm_reject", {}),
            (f"/payments/{self.payment.id}/eng_approve", {}),
            (f"/payments/{self.payment.id}/eng_reject", {}),
            (f"/payments/{self.payment.id}/finance_approve", {}),
            (f"/payments/{self.payment.id}/finance_reject", {}),
            (f"/payments/{self.payment.id}/mark_paid", {}),
            (f"/payments/{self.payment.id}/finance-amount", {"finance_amount": "100"}),
            (f"/payments/{self.payment.id}/add_notification_note", {"note": "test"}),
        ]

        for endpoint, payload in endpoints:
            response = self.client.post(endpoint, data=payload)
            self.assertEqual(response.status_code, 403, msg=f"Expected 403 for {endpoint}")


if __name__ == "__main__":
    unittest.main()
