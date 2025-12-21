import unittest
from datetime import datetime, timedelta

from app import create_app
from config import Config
from extensions import db
from models import PaymentRequest, Project, Supplier, Role, User
from project_scopes import get_scoped_project_ids
from blueprints.payments import routes as payment_routes


class InboxTestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "inbox-secret"
    WTF_CSRF_ENABLED = False


class PaymentsInboxTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(InboxTestConfig)
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
                "chairman",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Inbox Project")
        self.supplier = Supplier(name="Inbox Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.users = {
            name: self._create_user(f"{name}@example.com", self.roles[name])
            for name in self.roles
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
        return user

    def _assign_projects(self, user: User, projects: list[Project]):
        user.projects = projects
        if projects:
            user.project_id = projects[0].id
        db.session.commit()

    def _assign_projects(self, user: User, projects: list[Project]):
        user.projects = projects
        if projects:
            user.project_id = projects[0].id
        db.session.commit()

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _make_payment(
        self,
        status: str,
        *,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        created_by: int | None = None,
    ) -> PaymentRequest:
        payment = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=1000.0,
            description="desc",
            status=status,
            created_by=created_by,
        )
        if created_at:
            payment.created_at = created_at
        if updated_at:
            payment.updated_at = updated_at
        db.session.add(payment)
        db.session.commit()
        return payment

    def test_action_required_lists_matching_statuses(self):
        eng_manager = self.users["engineering_manager"]
        self._make_payment(payment_routes.STATUS_PENDING_PM, created_by=eng_manager.id)
        self._make_payment(payment_routes.STATUS_PAID, created_by=eng_manager.id)

        self._login(eng_manager)
        response = self.client.get("/payments/inbox/action-required")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-payment-id="1"', body)
        self.assertNotIn('data-payment-id="2"', body)

    def test_overdue_lists_only_late_items(self):
        admin = self.users["admin"]
        old_ts = datetime.utcnow() - timedelta(days=10)
        fresh_ts = datetime.utcnow() - timedelta(days=1)
        self._make_payment(
            payment_routes.STATUS_PENDING_PM,
            created_by=admin.id,
            updated_at=old_ts,
            created_at=old_ts,
        )
        self._make_payment(
            payment_routes.STATUS_PENDING_PM,
            created_by=admin.id,
            updated_at=fresh_ts,
            created_at=fresh_ts,
        )

        self._login(admin)
        response = self.client.get("/payments/inbox/overdue")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-payment-id="1"', body)
        self.assertNotIn('data-payment-id="2"', body)

    def test_ready_for_payment_requires_finance_roles(self):
        pm_user = self.users["project_manager"]
        self._login(pm_user)
        response = self.client.get("/payments/inbox/ready-for-payment")
        self.assertEqual(response.status_code, 403)

    def test_engineer_inbox_respects_multiple_projects(self):
        engineer = self.users["engineer"]

        other_project = Project(project_name="Inbox Project 2")
        third_project = Project(project_name="Inbox Project 3")
        db.session.add_all([other_project, third_project])
        db.session.commit()

        self._assign_projects(engineer, [self.project, other_project])

        # payments across projects the engineer should see
        in_scope_action = self._make_payment(payment_routes.STATUS_PENDING_PM, created_by=engineer.id)
        in_scope_overdue = self._make_payment(
            payment_routes.STATUS_PENDING_PM,
            created_by=engineer.id,
            updated_at=datetime.utcnow() - timedelta(days=10),
        )

        # payment in third project (not assigned) should be hidden
        out_of_scope_payment = PaymentRequest(
            project=third_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=123,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=engineer.id,
        )
        db.session.add(out_of_scope_payment)
        db.session.commit()

        self._login(engineer)

        action_resp = self.client.get("/payments/inbox/action-required")
        action_body = action_resp.get_data(as_text=True)
        self.assertEqual(action_resp.status_code, 200)
        self.assertIn(f'data-payment-id="{in_scope_action.id}"', action_body)
        self.assertNotIn(f'data-payment-id="{out_of_scope_payment.id}"', action_body)

        overdue_resp = self.client.get("/payments/inbox/overdue")
        overdue_body = overdue_resp.get_data(as_text=True)
        self.assertEqual(overdue_resp.status_code, 200)
        self.assertIn(f'data-payment-id="{in_scope_overdue.id}"', overdue_body)
        self.assertNotIn(f'data-payment-id="{out_of_scope_payment.id}"', overdue_body)

        scoped_ids = get_scoped_project_ids(engineer, role_name="engineer")
        self.assertEqual(set(scoped_ids), {self.project.id, other_project.id})

    def test_engineer_inbox_respects_multiple_projects(self):
        engineer = self.users["engineer"]

        other_project = Project(project_name="Inbox Project 2")
        third_project = Project(project_name="Inbox Project 3")
        db.session.add_all([other_project, third_project])
        db.session.commit()

        self._assign_projects(engineer, [self.project, other_project])

        # payments across projects the engineer should see
        in_scope_action = self._make_payment(payment_routes.STATUS_PENDING_PM, created_by=engineer.id)
        in_scope_overdue = self._make_payment(
            payment_routes.STATUS_PENDING_PM,
            created_by=engineer.id,
            updated_at=datetime.utcnow() - timedelta(days=10),
        )

        # payment in third project (not assigned) should be hidden
        out_of_scope_payment = PaymentRequest(
            project=third_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=123,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=engineer.id,
        )
        db.session.add(out_of_scope_payment)
        db.session.commit()

        self._login(engineer)

        action_resp = self.client.get("/payments/inbox/action-required")
        action_body = action_resp.get_data(as_text=True)
        self.assertEqual(action_resp.status_code, 200)
        self.assertIn(f'data-payment-id="{in_scope_action.id}"', action_body)
        self.assertNotIn(f'data-payment-id="{out_of_scope_payment.id}"', action_body)

        overdue_resp = self.client.get("/payments/inbox/overdue")
        overdue_body = overdue_resp.get_data(as_text=True)
        self.assertEqual(overdue_resp.status_code, 200)
        self.assertIn(f'data-payment-id="{in_scope_overdue.id}"', overdue_body)
        self.assertNotIn(f'data-payment-id="{out_of_scope_payment.id}"', overdue_body)

        scoped_ids = get_scoped_project_ids(engineer, role_name="engineer")
        self.assertEqual(set(scoped_ids), {self.project.id, other_project.id})

    def test_dashboard_links_point_to_inbox_routes(self):
        admin = self.users["admin"]
        # seed counts so chips render with links
        self._make_payment(payment_routes.STATUS_PENDING_PM, created_by=admin.id)
        self._make_payment(payment_routes.STATUS_READY_FOR_PAYMENT, created_by=admin.id)

        self._login(admin)
        response = self.client.get("/dashboard")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("/payments/inbox/action-required", body)
        self.assertIn("/payments/inbox/overdue", body)
        self.assertIn("/payments/inbox/ready-for-payment", body)

    def test_ready_for_payment_pagination(self):
        finance_user = self.users["finance"]
        now = datetime.utcnow()
        for idx in range(25):
            ts = now - timedelta(minutes=idx)
            self._make_payment(
                payment_routes.STATUS_READY_FOR_PAYMENT,
                created_by=finance_user.id,
                created_at=ts,
                updated_at=ts,
            )

        self._login(finance_user)
        response = self.client.get("/payments/inbox/ready-for-payment?per_page=20&page=2")
        body = response.get_data(as_text=True)
        max_id = db.session.query(PaymentRequest.id).order_by(PaymentRequest.id.desc()).first()[0]

        self.assertEqual(response.status_code, 200)
        # second page should have 5 rows (oldest 5) and include the oldest ids
        self.assertIn(f'data-payment-id="{max_id}"', body)
        self.assertNotIn('data-payment-id="1"', body)
        self.assertEqual(body.count('data-payment-id="'), 5)


if __name__ == "__main__":
    unittest.main()
