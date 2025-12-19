import unittest

from app import create_app
from blueprints.payments import routes as payment_routes
from config import Config
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PaymentsStickyFiltersTestCase(unittest.TestCase):
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

        sample_payments = [
            PaymentRequest(
                project=self.project,
                supplier=self.supplier,
                request_type="contractor",
                amount=100 + i,
                status=payment_routes.STATUS_PENDING_PM,
                created_by=self.admin.id,
            )
            for i in range(60)
        ]
        db.session.add_all(sample_payments)
        db.session.commit()

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

    def test_selected_filter_values_render_in_listing(self):
        self._login(self.admin)
        response = self.client.get(
            f"/payments/all?status={payment_routes.STATUS_PENDING_PM}&per_page=50"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'<option value="{payment_routes.STATUS_PENDING_PM}" selected', body
        )
        self.assertRegex(body, r'<option value="50"[^>]*selected')


if __name__ == "__main__":
    unittest.main()
