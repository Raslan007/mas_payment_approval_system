import unittest

from app import create_app
from config import Config
from extensions import db
from models import Notification, PaymentRequest, Project, Role, Supplier, User
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class NotificationGenerationTestCase(unittest.TestCase):
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
                "engineering_manager",
                "project_manager",
                "engineer",
                "finance",
                "payment_notifier",
            ]
        }
        db.session.add_all(self.roles.values())

        self.project = Project(project_name="Project Alpha")
        self.alt_project = Project(project_name="Project Beta")
        self.supplier = Supplier(name="Supplier", supplier_type="contractor")
        db.session.add_all([self.project, self.alt_project, self.supplier])
        db.session.commit()

        self.users = {
            name: self._create_user(f"{name}@example.com", self.roles[name], project=self.project)
            for name in self.roles
        }
        self.users["alt_pm"] = self._create_user(
            "alt_pm@example.com",
            self.roles["project_manager"],
            project=self.alt_project,
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _create_user(
        self,
        email: str,
        role: Role,
        project: Project | None = None,
    ) -> User:
        project_id = project.id if project else None
        user = User(
            full_name=email.split("@")[0],
            email=email,
            role=role,
            project_id=project_id,
        )
        user.set_password("password")
        db.session.add(user)
        if role.name == "project_manager" and project:
            user.projects = [project]
        db.session.commit()
        return user

    def _login(self, user: User):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _make_payment(self, status: str, created_by: int | None) -> PaymentRequest:
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

    def _count_notifications(self, user: User) -> int:
        return Notification.query.filter_by(user_id=user.id).count()

    def test_status_transitions_create_notifications_for_expected_recipients(self):
        admin = self.users["admin"]
        project_manager = self.users["project_manager"]
        engineer = self.users["engineer"]
        eng_manager = self.users["engineering_manager"]
        finance = self.users["finance"]
        notifier = self.users["payment_notifier"]
        alt_pm = self.users["alt_pm"]

        payment_submit = self._make_payment(payment_routes.STATUS_DRAFT, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}

        self._login(engineer)
        response = self.client.post(f"/payments/{payment_submit.id}/submit_to_pm")
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(project_manager), before_counts[project_manager.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)
        self.assertEqual(self._count_notifications(alt_pm), before_counts[alt_pm.id])

        payment_pm = self._make_payment(payment_routes.STATUS_PENDING_PM, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}
        payment_routes._create_notifications(
            payment_pm,
            title=f"اعتماد مدير المشروع للدفعة رقم {payment_pm.id}",
            message=f"تم تحويل الحالة إلى {payment_pm.human_status}.",
            url=f"/payments/{payment_pm.id}",
            roles=("engineering_manager",),
            include_creator=True,
        )
        db.session.commit()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(eng_manager), before_counts[eng_manager.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)

        payment_eng = self._make_payment(payment_routes.STATUS_PENDING_ENG, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}
        payment_routes._create_notifications(
            payment_eng,
            title=f"اعتماد الإدارة الهندسية للدفعة رقم {payment_eng.id}",
            message=f"تم تحويل الحالة إلى {payment_eng.human_status}.",
            url=f"/payments/{payment_eng.id}",
            roles=("finance",),
            include_creator=True,
        )
        db.session.commit()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(finance), before_counts[finance.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)

        payment_finance_amount = self._make_payment(payment_routes.STATUS_PENDING_FIN, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}
        payment_routes._create_notifications(
            payment_finance_amount,
            title=f"تحديث مبلغ المالية للدفعة رقم {payment_finance_amount.id}",
            message="تم تعديل مبلغ المالية من 0 إلى 900.",
            url=f"/payments/{payment_finance_amount.id}",
            roles=("project_manager",),
            include_creator=True,
        )
        db.session.commit()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(project_manager), before_counts[project_manager.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)

        payment_finance = self._make_payment(payment_routes.STATUS_PENDING_FIN, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}
        payment_routes._create_notifications(
            payment_finance,
            title=f"اعتماد المالية للدفعة رقم {payment_finance.id}",
            message="أصبحت الدفعة جاهزة للصرف.",
            url=f"/payments/{payment_finance.id}",
            roles=("payment_notifier",),
            include_creator=True,
        )
        db.session.commit()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(notifier), before_counts[notifier.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)

        payment_note = self._make_payment(payment_routes.STATUS_READY_FOR_PAYMENT, engineer.id)
        before_counts = {user.id: self._count_notifications(user) for user in self.users.values()}
        payment_routes._create_notifications(
            payment_note,
            title=f"ملاحظة إشعار للدفعة رقم {payment_note.id}",
            message="تم التواصل مع المقاول",
            url=f"/payments/{payment_note.id}",
            roles=("finance", "project_manager"),
            include_creator=True,
        )
        db.session.commit()

        self.assertEqual(self._count_notifications(admin), before_counts[admin.id] + 1)
        self.assertEqual(self._count_notifications(finance), before_counts[finance.id] + 1)
        self.assertEqual(self._count_notifications(engineer), before_counts[engineer.id] + 1)
