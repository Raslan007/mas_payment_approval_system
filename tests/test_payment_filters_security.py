import re
import unittest

from config import Config
from app import create_app
from extensions import db
from models import PaymentRequest, Project, Role, Supplier, User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PaymentFiltersSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.roles = {
            name: Role(name=name)
            for name in [
                "admin",
                "project_manager",
                "engineer",
            ]
        }
        db.session.add_all(self.roles.values())

        self.projects = [
            Project(project_name="Alpha"),
            Project(project_name="Beta"),
        ]
        self.supplier = Supplier(name="Acme", supplier_type="contractor")
        db.session.add_all([*self.projects, self.supplier])
        db.session.commit()

        self.admin = self._create_user("admin@example.com", self.roles["admin"])
        self.pm = self._create_user(
            "pm@example.com",
            self.roles["project_manager"],
            project=self.projects[0],
        )
        # ربط مدير المشروع بمشروعه الأساسي في جدول الربط
        self.pm.projects.append(self.projects[0])
        self.engineer_one = self._create_user(
            "eng1@example.com", self.roles["engineer"], project=self.projects[0]
        )
        self.engineer_two = self._create_user(
            "eng2@example.com", self.roles["engineer"], project=self.projects[1]
        )
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

    def test_pm_cannot_see_unassigned_project_even_with_filter(self):
        my_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=100,
            status="pending_pm",
            created_by=self.pm.id,
        )
        other_payment = PaymentRequest(
            project=self.projects[1],
            supplier=self.supplier,
            request_type="contractor",
            amount=200,
            status="pending_pm",
            created_by=self.engineer_two.id,
        )
        db.session.add_all([my_payment, other_payment])
        db.session.commit()

        self._login(self.pm)
        response = self.client.get(f"/payments/?project_id={self.projects[1].id}")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(my_payment.id), body)
        self.assertNotRegex(body, rf'data-payment-id="{other_payment.id}"')

    def test_engineer_only_sees_own_items_when_filtering(self):
        mine = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=50,
            status="draft",
            created_by=self.engineer_one.id,
        )
        someone_else = PaymentRequest(
            project=self.projects[1],
            supplier=self.supplier,
            request_type="contractor",
            amount=75,
            status="draft",
            created_by=self.engineer_two.id,
        )
        db.session.add_all([mine, someone_else])
        db.session.commit()

        self._login(self.engineer_one)
        response = self.client.get(f"/payments/?project_id={self.projects[1].id}")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("لا توجد دفعات", body)
        self.assertNotRegex(body, rf'data-payment-id="{someone_else.id}"')

    def test_invalid_query_params_are_sanitized(self):
        payments = [
            PaymentRequest(
                project=self.projects[i % 2],
                supplier=self.supplier,
                request_type="contractor",
                amount=10 + i,
                created_by=self.admin.id,
            )
            for i in range(120)
        ]
        db.session.add_all(payments)
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(
            "/payments/?page=-5&per_page=5000&status=invalid&week_number=abc&date_from=bad&date_to=2024-13-01"
        )

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        rendered_payments = re.findall(r"data-payment-id=\"(\d+)\"", body)
        # per_page يجب أن يتم تقليمه إلى 100 حتى مع قيم غير صحيحة
        self.assertEqual(len(rendered_payments), 100)
        # IDs مرتبة تنازليًا حسب created_at ثم id
        rendered_ids = list(map(int, rendered_payments))
        self.assertEqual(rendered_ids[0], payments[-1].id)
        self.assertTrue(all(earlier >= later for earlier, later in zip(rendered_ids, rendered_ids[1:])))


if __name__ == "__main__":
    unittest.main()
