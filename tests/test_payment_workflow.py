import re
import unittest

from app import create_app
from config import Config
from extensions import db
from models import (
    PaymentNotificationNote,
    PaymentRequest,
    Project,
    Supplier,
    Role,
    User,
)
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PaymentWorkflowTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        # seed minimal reference data
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
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        self.alt_project = Project(project_name="Alt Project")
        db.session.add_all([self.project, self.alt_project, self.supplier])
        db.session.commit()

        # cache users per role
        self.users = {
            name: self._create_user(f"{name}@example.com", self.roles[name])
            for name in self.roles
        }

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(
        self,
        email: str,
        role: Role,
        project: Project | None = None,
        projects: list[Project] | None = None,
    ) -> User:
        project_list = projects or ([] if project is None else [project])
        if not project_list and role.name in ("engineer", "project_manager"):
            project_list = [self.project]

        project_id = project_list[0].id if project_list else None

        user = User(
            full_name=email.split("@")[0],
            email=email,
            role=role,
            project_id=project_id,
        )
        user.set_password("password")
        db.session.add(user)
        if role.name == "project_manager":
            user.projects = project_list
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
            amount=1000.0,
            description="desc",
            status=status,
            created_by=created_by,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_submit_to_pm_allows_engineer(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._login(self.users["engineer"])

        response = self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self.assertEqual(response.status_code, 302)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.status, payment_routes.STATUS_PENDING_PM)

    def test_pm_approve_requires_pending_pm_state(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["project_manager"].id)
        self._login(self.users["project_manager"])

        response = self.client.post(f"/payments/{payment.id}/pm_approve")
        self.assertEqual(response.status_code, 302)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.status, payment_routes.STATUS_DRAFT)

    def test_pm_approve_advances_to_engineering(self):
        payment = self._make_payment(
            payment_routes.STATUS_PENDING_PM, self.users["project_manager"].id
        )
        self._login(self.users["project_manager"])

        response = self.client.post(f"/payments/{payment.id}/pm_approve")
        self.assertEqual(response.status_code, 302)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.status, payment_routes.STATUS_PENDING_ENG)

    def test_finance_approve_reject_and_paid_flow(self):
        # move through finance steps and ensure guards keep status order
        payment = self._make_payment(payment_routes.STATUS_PENDING_FIN)

        # finance approve: pending_fin -> ready_for_payment
        self._login(self.users["finance"])
        resp = self.client.post(f"/payments/{payment.id}/finance_approve")
        self.assertEqual(resp.status_code, 302)
        db.session.refresh(payment)
        self.assertEqual(payment.status, payment_routes.STATUS_READY_FOR_PAYMENT)

        # finance reject should now be blocked because status changed
        resp_reject = self.client.post(f"/payments/{payment.id}/finance_reject")
        self.assertEqual(resp_reject.status_code, 302)
        db.session.refresh(payment)
        self.assertEqual(payment.status, payment_routes.STATUS_READY_FOR_PAYMENT)

        # mark paid with valid amount
        resp_paid = self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"amount_finance": "1200"},
        )
        self.assertEqual(resp_paid.status_code, 302)
        db.session.refresh(payment)
        self.assertEqual(payment.status, payment_routes.STATUS_PAID)
        self.assertEqual(payment.amount_finance, 1200.0)

    def test_engineer_cannot_access_finance_endpoint(self):
        payment = self._make_payment(payment_routes.STATUS_PENDING_FIN)
        self._login(self.users["engineer"])

        response = self.client.post(f"/payments/{payment.id}/finance_approve")
        self.assertEqual(response.status_code, 403)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.status, payment_routes.STATUS_PENDING_FIN)

    def test_payment_notifier_listing_is_restricted(self):
        ready_payment = self._make_payment(payment_routes.STATUS_READY_FOR_PAYMENT)
        paid_payment = self._make_payment(payment_routes.STATUS_PAID)
        hidden_payment = self._make_payment(payment_routes.STATUS_PENDING_PM)

        self._login(self.users["payment_notifier"])

        response = self.client.get("/payments/?per_page=20")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        row_ids = re.findall(r"<td class=\"text-center text-muted\">\s*(\d+)", body)

        self.assertIn(str(ready_payment.id), row_ids)
        self.assertIn(str(paid_payment.id), row_ids)
        self.assertNotIn(str(hidden_payment.id), row_ids)

        blocked_detail = self.client.get(f"/payments/{hidden_payment.id}")
        self.assertEqual(blocked_detail.status_code, 404)

    def test_payment_notifier_cannot_use_approval_endpoints(self):
        payment = self._make_payment(payment_routes.STATUS_PENDING_PM)
        self._login(self.users["payment_notifier"])

        response = self.client.post(f"/payments/{payment.id}/pm_approve")
        self.assertEqual(response.status_code, 403)

        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed.status, payment_routes.STATUS_PENDING_PM)

    def test_payment_notifier_can_add_notification_note_on_allowed_status(self):
        payment = self._make_payment(payment_routes.STATUS_READY_FOR_PAYMENT)
        blocked_payment = self._make_payment(payment_routes.STATUS_PENDING_PM)

        self._login(self.users["payment_notifier"])

        response = self.client.post(
            f"/payments/{payment.id}/add_notification_note",
            data={"note": "Contractor notified"},
        )
        self.assertEqual(response.status_code, 302)

        notes = PaymentNotificationNote.query.filter_by(
            payment_request_id=payment.id
        ).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].user_id, self.users["payment_notifier"].id)
        self.assertIn("Contractor notified", notes[0].note)

        blocked_resp = self.client.post(
            f"/payments/{blocked_payment.id}/add_notification_note",
            data={"note": "Should be blocked"},
        )
        self.assertEqual(blocked_resp.status_code, 404)

    def test_project_manager_cannot_view_other_project_payment(self):
        other_project = Project(project_name="Other Project")
        db.session.add(other_project)
        db.session.commit()

        outsider_pm = self._create_user(
            "outsider_pm@example.com", self.roles["project_manager"], other_project
        )

        payment = PaymentRequest(
            project=other_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=500,
            description="desc",
            status=payment_routes.STATUS_PENDING_PM,
            created_by=outsider_pm.id,
        )
        db.session.add(payment)
        db.session.commit()

        # logged in PM belongs to default project, should not access other project's payment
        self._login(self.users["project_manager"])
        resp = self.client.get(f"/payments/{payment.id}")
        self.assertEqual(resp.status_code, 404)

    def test_project_manager_listing_only_shows_own_project(self):
        other_project = Project(project_name="Other Project")
        db.session.add(other_project)
        db.session.commit()

        outsider_pm = self._create_user(
            "pm2@example.com", self.roles["project_manager"], other_project
        )

        own_payment = self._make_payment(
            payment_routes.STATUS_PENDING_PM, self.users["project_manager"].id
        )
        other_payment = PaymentRequest(
            project=other_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=750,
            description="other",
            status=payment_routes.STATUS_PENDING_PM,
            created_by=outsider_pm.id,
        )
        db.session.add(other_payment)
        db.session.commit()

        self._login(self.users["project_manager"])
        resp = self.client.get("/payments/?per_page=10")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        row_ids = re.findall(r"<td class=\"text-center text-muted\">\s*(\d+)", body)
        self.assertIn(str(own_payment.id), row_ids)
        self.assertNotIn(str(other_payment.id), row_ids)

    def test_project_manager_with_multiple_projects_sees_all_assigned(self):
        third_project = Project(project_name="Third Project")
        db.session.add(third_project)
        db.session.commit()

        pm_multi = self._create_user(
            "multi_pm@example.com",
            self.roles["project_manager"],
            projects=[self.project, self.alt_project],
        )

        payment_a = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=900,
            description="proj a",
            status=payment_routes.STATUS_PENDING_PM,
            created_by=pm_multi.id,
        )
        payment_b = PaymentRequest(
            project=self.alt_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=800,
            description="proj b",
            status=payment_routes.STATUS_PENDING_PM,
            created_by=pm_multi.id,
        )
        payment_c = PaymentRequest(
            project=third_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=700,
            description="proj c",
            status=payment_routes.STATUS_PENDING_PM,
            created_by=pm_multi.id,
        )
        db.session.add_all([payment_a, payment_b, payment_c])
        db.session.commit()

        self._login(pm_multi)

        resp_a = self.client.get(f"/payments/{payment_a.id}")
        resp_b = self.client.get(f"/payments/{payment_b.id}")
        resp_c = self.client.get(f"/payments/{payment_c.id}")

        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_c.status_code, 404)

    def test_full_positive_workflow(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["admin"].id)

        # Admin can drive the whole flow when acting as a superuser
        self._login(self.users["admin"])
        self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self.client.post(f"/payments/{payment.id}/pm_approve")
        self.client.post(f"/payments/{payment.id}/eng_approve")

        # Finance approves to ready_for_payment and marks paid
        self._login(self.users["finance"])
        self.client.post(f"/payments/{payment.id}/finance_approve")
        self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"amount_finance": "1000"},
        )

        final = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(final.status, payment_routes.STATUS_PAID)
        self.assertEqual(final.amount_finance, 1000.0)


if __name__ == "__main__":
    unittest.main()
