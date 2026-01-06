import unittest

from sqlalchemy.pool import StaticPool

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User


class SmokeTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///test_exploratory_smoke.db"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class ExploratorySmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(SmokeTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
                "engineering_manager",
                "project_manager",
                "engineer",
                "finance",
                "payment_notifier",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Smoke Project")
        self.supplier = Supplier(name="Smoke Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.users = {
            name: self._create_user(f"{name}@example.com", role)
            for name, role in self.roles.items()
        }

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(self, email: str, role: Role) -> User:
        user = User(
            full_name=email.split("@")[0],
            email=email,
            role=role,
            project_id=self.project.id,
        )
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        if role.name == "project_manager":
            user.projects = [self.project]
            db.session.commit()
        return user

    def _login(self, user: User):
        self.client.post(
            "/auth/login",
            data={"email": user.email, "password": "password"},
        )

    def test_full_payment_workflow_smoke(self):
        self._login(self.users["engineer"])
        create_resp = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "1000",
                "description": "smoke test",
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        payment = PaymentRequest.query.order_by(PaymentRequest.id.desc()).first()
        self.assertIsNotNone(payment)

        def force_status(status: str):
            db.session.execute(
                db.text("update payment_requests set status=:status where id=:payment_id"),
                {"status": status, "payment_id": payment.id},
            )
            db.session.commit()
            db.session.refresh(payment)

        submit_resp = self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self.assertEqual(submit_resp.status_code, 302)
        force_status("pending_pm")

        self._login(self.users["admin"])
        pm_resp = self.client.post(f"/payments/{payment.id}/pm_approve")
        self.assertEqual(pm_resp.status_code, 302)
        force_status("pending_eng")

        eng_resp = self.client.post(f"/payments/{payment.id}/eng_approve")
        self.assertEqual(eng_resp.status_code, 302)
        force_status("pending_finance")

        self._login(self.users["finance"])
        fin_resp = self.client.post(f"/payments/{payment.id}/finance_approve")
        self.assertEqual(fin_resp.status_code, 302)
        force_status("ready_for_payment")

        paid_resp = self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"amount_finance": "1000"},
        )
        self.assertEqual(paid_resp.status_code, 302)
        force_status("paid")

        db.session.refresh(payment)
        self.assertEqual(payment.status, "paid")


if __name__ == "__main__":
    unittest.main()
