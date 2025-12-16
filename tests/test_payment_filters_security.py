import re
import unittest
from datetime import datetime

from config import Config
from app import create_app
from extensions import db
from models import PaymentApproval, PaymentRequest, Project, Role, Supplier, User


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

    def test_week_number_filter_uses_submission_iso_week(self):
        reference_year = datetime.utcnow().isocalendar().year
        target_week = 10
        other_week = 12

        submission_date = datetime.fromisocalendar(reference_year, target_week, 3)
        other_submission = datetime.fromisocalendar(reference_year, other_week, 3)

        week_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=150,
            status="draft",
            created_by=self.admin.id,
            created_at=submission_date,
            submitted_to_pm_at=submission_date,
        )
        null_submission_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=200,
            status="draft",
            created_by=self.admin.id,
            created_at=submission_date,
            submitted_to_pm_at=None,
        )
        another_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=250,
            status="draft",
            created_by=self.admin.id,
            created_at=submission_date,
            submitted_to_pm_at=other_submission,
        )
        db.session.add_all([week_payment, null_submission_payment, another_payment])
        db.session.flush()

        db.session.add_all(
            [
                PaymentApproval(
                    payment_request_id=week_payment.id,
                    step="engineer",
                    action="submit",
                    decided_at=submission_date,
                ),
                PaymentApproval(
                    payment_request_id=another_payment.id,
                    step="engineer",
                    action="submit",
                    decided_at=other_submission,
                ),
            ]
        )
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(f"/payments/?week_number={target_week}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, rf'data-payment-id="{week_payment.id}"')
        self.assertNotRegex(body, rf'data-payment-id="{null_submission_payment.id}"')
        self.assertNotRegex(body, rf'data-payment-id="{another_payment.id}"')

    def test_my_payments_without_week_number_returns_results(self):
        reference_year = datetime.utcnow().isocalendar().year
        submit_day = datetime.fromisocalendar(reference_year, 30, 3)

        payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=210,
            status="pending_pm",
            created_by=self.admin.id,
            created_at=submit_day,
            submitted_to_pm_at=submit_day,
        )
        db.session.add(payment)
        db.session.commit()

        self._login(self.admin)
        response = self.client.get("/payments/my")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, rf'data-payment-id="{payment.id}"')

    def test_my_payments_week_number_filter_still_applies(self):
        reference_year = datetime.utcnow().isocalendar().year
        desired_week = 12

        matched_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=310,
            status="pending_pm",
            created_by=self.admin.id,
            created_at=datetime.fromisocalendar(reference_year, desired_week, 2),
            submitted_to_pm_at=datetime.fromisocalendar(reference_year, desired_week, 2),
        )
        other_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=320,
            status="pending_pm",
            created_by=self.admin.id,
            created_at=datetime.fromisocalendar(reference_year, desired_week + 1, 3),
            submitted_to_pm_at=datetime.fromisocalendar(reference_year, desired_week + 1, 3),
        )
        db.session.add_all([matched_payment, other_payment])
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(f"/payments/my?week_number={desired_week}")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, rf'data-payment-id="{matched_payment.id}"')
        self.assertNotRegex(body, rf'data-payment-id="{other_payment.id}"')

    def test_week_number_filter_applies_in_my_route_and_keeps_pagination_params(self):
        reference_year = datetime.utcnow().isocalendar().year
        target_week = 8

        submit_day_one = datetime.fromisocalendar(reference_year, target_week, 2)
        submit_day_two = datetime.fromisocalendar(reference_year, target_week, 4)
        other_week_submit = datetime.fromisocalendar(reference_year, target_week + 1, 3)

        first_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=110,
            status="draft",
            created_by=self.admin.id,
            created_at=submit_day_one,
            submitted_to_pm_at=submit_day_one,
        )
        second_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=120,
            status="draft",
            created_by=self.admin.id,
            created_at=submit_day_two,
            submitted_to_pm_at=submit_day_two,
        )
        other_week_payment = PaymentRequest(
            project=self.projects[0],
            supplier=self.supplier,
            request_type="contractor",
            amount=130,
            status="draft",
            created_by=self.admin.id,
            created_at=submit_day_one,
            submitted_to_pm_at=other_week_submit,
        )

        db.session.add_all([first_payment, second_payment, other_week_payment])
        db.session.flush()

        db.session.add_all(
            [
                PaymentApproval(
                    payment_request_id=first_payment.id,
                    step="engineer",
                    action="submit",
                    decided_at=submit_day_one,
                ),
                PaymentApproval(
                    payment_request_id=second_payment.id,
                    step="engineer",
                    action="submit",
                    decided_at=submit_day_two,
                ),
                PaymentApproval(
                    payment_request_id=other_week_payment.id,
                    step="engineer",
                    action="submit",
                    decided_at=other_week_submit,
                ),
            ]
        )
        db.session.commit()

        self._login(self.admin)
        response = self.client.get(
            f"/payments/my?week_number={target_week}&per_page=1"
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            body,
            rf'data-payment-id="({first_payment.id}|{second_payment.id})"',
        )
        self.assertNotRegex(body, rf'data-payment-id="{other_week_payment.id}"')
        self.assertRegex(body, rf"week_number={target_week}")

        page_two_response = self.client.get(
            f"/payments/my?week_number={target_week}&per_page=1&page=2"
        )
        page_two_body = page_two_response.get_data(as_text=True)

        self.assertEqual(page_two_response.status_code, 200)
        self.assertRegex(
            page_two_body,
            rf'data-payment-id="({first_payment.id}|{second_payment.id})"',
        )
        self.assertNotRegex(page_two_body, rf'data-payment-id="{other_week_payment.id}"')
        self.assertRegex(page_two_body, rf"week_number={target_week}")


if __name__ == "__main__":
    unittest.main()
