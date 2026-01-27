import re
import unittest

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


class PaymentsAllFiltersTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Main Project")
        self.supplier = Supplier(name="Acme", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])

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

    def test_status_filter_reduces_results(self):
        pending_pm = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=100,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        pending_eng = PaymentRequest(
            project=self.project,
            supplier=self.supplier,
            request_type="contractor",
            amount=150,
            status=payment_routes.STATUS_PENDING_ENG,
            created_by=self.admin.id,
        )
        db.session.add_all([pending_pm, pending_eng])
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(f"/payments/all?status={payment_routes.STATUS_PENDING_PM}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, rf'data-payment-id="{pending_pm.id}"')
        self.assertNotRegex(body, rf'data-payment-id="{pending_eng.id}"')

    def test_pagination_links_preserve_filters(self):
        payments = [
            PaymentRequest(
                project=self.project,
                supplier=self.supplier,
                request_type="contractor",
                amount=10 + i,
                status=payment_routes.STATUS_PENDING_PM,
                created_by=self.admin.id,
            )
            for i in range(2)
        ]
        db.session.add_all(payments)
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(
            f"/payments/all?status={payment_routes.STATUS_PENDING_PM}&per_page=1"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, r"status=pending_pm")
        self.assertRegex(body, rf'data-payment-id="{payments[1].id}"|data-payment-id="{payments[0].id}"')

        page_two_response = self.client.get(
            f"/payments/all?status={payment_routes.STATUS_PENDING_PM}&per_page=1&page=2"
        )
        page_two_body = page_two_response.get_data(as_text=True)

        self.assertEqual(page_two_response.status_code, 200)
        self.assertRegex(page_two_body, r"status=pending_pm")
        self.assertRegex(
            page_two_body,
            rf'data-payment-id="{payments[1].id}"|data-payment-id="{payments[0].id}"',
        )

    def test_sorting_by_vendor_name(self):
        alpha_supplier = Supplier(name="Alpha Vendor", supplier_type="contractor")
        zulu_supplier = Supplier(name="zulu vendor", supplier_type="contractor")
        db.session.add_all([alpha_supplier, zulu_supplier])
        db.session.commit()

        alpha_payment = PaymentRequest(
            project=self.project,
            supplier=alpha_supplier,
            request_type="contractor",
            amount=200,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        zulu_payment = PaymentRequest(
            project=self.project,
            supplier=zulu_supplier,
            request_type="contractor",
            amount=300,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        db.session.add_all([alpha_payment, zulu_payment])
        db.session.commit()

        self._login(self.admin)
        response = self.client.get("/payments/all?sort=vendor&dir=asc")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        alpha_index = body.find(f'data-payment-id="{alpha_payment.id}"')
        zulu_index = body.find(f'data-payment-id="{zulu_payment.id}"')
        self.assertNotEqual(alpha_index, -1)
        self.assertNotEqual(zulu_index, -1)
        self.assertLess(alpha_index, zulu_index)

    def test_sorting_by_project_name(self):
        alpha_project = Project(project_name="Alpha Project")
        zulu_project = Project(project_name="zulu project")
        db.session.add_all([alpha_project, zulu_project])
        db.session.commit()

        alpha_payment = PaymentRequest(
            project=alpha_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=200,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        zulu_payment = PaymentRequest(
            project=zulu_project,
            supplier=self.supplier,
            request_type="contractor",
            amount=300,
            status=payment_routes.STATUS_PENDING_PM,
            created_by=self.admin.id,
        )
        db.session.add_all([alpha_payment, zulu_payment])
        db.session.commit()

        self._login(self.admin)
        response = self.client.get("/payments/all?sort=project&dir=asc")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        alpha_index = body.find(f'data-payment-id="{alpha_payment.id}"')
        zulu_index = body.find(f'data-payment-id="{zulu_payment.id}"')
        self.assertNotEqual(alpha_index, -1)
        self.assertNotEqual(zulu_index, -1)
        self.assertLess(alpha_index, zulu_index)


if __name__ == "__main__":
    unittest.main()
