import html
import re
import unittest
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from app import create_app
from config import Config
from extensions import db
from models import (
    Project,
    PurchaseOrder,
    Role,
    Supplier,
    User,
    PURCHASE_ORDER_STATUS_DRAFT,
)


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class PurchaseOrderCloneTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

        self.procurement_role = Role(name="procurement")
        self.engineer_role = Role(name="engineer")
        db.session.add_all([self.procurement_role, self.engineer_role])

        self.project = Project(project_name="Alpha")
        self.supplier = Supplier(name="Vendor A", supplier_type="contractor")
        self.other_supplier = Supplier(name="Vendor B", supplier_type="contractor")
        db.session.add_all([self.project, self.supplier, self.other_supplier])
        db.session.commit()

        self.procurement_user = self._create_user(
            "procurement@example.com", self.procurement_role, self.project
        )
        self.engineer_user = self._create_user(
            "engineer@example.com", self.engineer_role, self.project
        )

        self.source_po = PurchaseOrder(
            bo_number="BO01174",
            description="توريد معدات المرحلة الأولى",
            project_id=self.project.id,
            supplier_id=self.supplier.id,
            supplier_name=self.supplier.name,
            total_amount=Decimal("500.00"),
            advance_amount=Decimal("50.00"),
            reserved_amount=Decimal("75.00"),
            paid_amount=Decimal("25.00"),
            remaining_amount=Decimal("450.00"),
            status=PURCHASE_ORDER_STATUS_DRAFT,
            created_by_id=self.procurement_user.id,
        )
        db.session.add(self.source_po)
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

    def test_clone_button_visible_for_editors(self):
        self._login(self.procurement_user)
        response = self.client.get(f"/purchase-orders/{self.source_po.id}")
        body = html.unescape(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn("إنشاء أمر شراء لمورد آخر لنفس المشروع", body)

    def test_clone_route_redirects_with_prefill(self):
        self._login(self.procurement_user)
        response = self.client.post(f"/purchase-orders/{self.source_po.id}/clone_for_other_vendor")

        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/purchase-orders/new")
        self.assertEqual(params.get("project_id"), [str(self.project.id)])
        self.assertEqual(params.get("reference_po_number"), [self.source_po.bo_number])
        self.assertEqual(params.get("description"), [self.source_po.description])

    def test_new_form_prefills_reference_and_description(self):
        self._login(self.procurement_user)
        response = self.client.get(
            "/purchase-orders/new",
            query_string={
                "project_id": self.project.id,
                "reference_po_number": self.source_po.bo_number,
                "description": self.source_po.description,
            },
        )
        body = html.unescape(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"مرجع: {self.source_po.bo_number}", body)
        self.assertIn(self.source_po.description, body)
        self.assertRegex(
            body,
            rf'<option value="{self.project.id}" selected>',
        )

    def test_clone_creates_new_purchase_order(self):
        self._login(self.procurement_user)
        response = self.client.post(
            "/purchase-orders/",
            data={
                "bo_number": "BO01999",
                "project_id": self.project.id,
                "supplier_id": self.other_supplier.id,
                "supplier_name": "",
                "total_amount": "200.00",
                "advance_amount": "0.00",
                "due_date": "",
                "description": self.source_po.description,
                "reference_po_number": self.source_po.bo_number,
            },
        )

        self.assertEqual(response.status_code, 302)
        new_po = PurchaseOrder.query.filter_by(bo_number="BO01999").first()
        self.assertIsNotNone(new_po)
        self.assertEqual(new_po.project_id, self.source_po.project_id)
        self.assertEqual(new_po.description, self.source_po.description)
        self.assertEqual(new_po.reference_po_number, self.source_po.bo_number)
        self.assertEqual(new_po.supplier_id, self.other_supplier.id)
        self.assertNotEqual(new_po.supplier_id, self.source_po.supplier_id)
        self.assertEqual(new_po.status, PURCHASE_ORDER_STATUS_DRAFT)
        self.assertNotEqual(new_po.reserved_amount, self.source_po.reserved_amount)
        self.assertNotEqual(new_po.paid_amount, self.source_po.paid_amount)

    def test_create_errors_preserve_prefill_query_params(self):
        self._login(self.procurement_user)
        response = self.client.post(
            "/purchase-orders/",
            data={
                "bo_number": "",
                "project_id": self.project.id,
                "supplier_id": self.other_supplier.id,
                "supplier_name": "",
                "total_amount": "200.00",
                "advance_amount": "0.00",
                "due_date": "",
                "description": self.source_po.description,
                "reference_po_number": self.source_po.bo_number,
            },
        )

        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/purchase-orders/new")
        self.assertEqual(params.get("project_id"), [str(self.project.id)])
        self.assertEqual(params.get("description"), [self.source_po.description])
        self.assertEqual(params.get("reference_po_number"), [self.source_po.bo_number])

    def test_clone_requires_permission(self):
        self._login(self.engineer_user)
        response = self.client.post(f"/purchase-orders/{self.source_po.id}/clone_for_other_vendor")

        self.assertEqual(response.status_code, 403)

    def test_edit_form_bo_number_is_readonly(self):
        self._login(self.procurement_user)
        response = self.client.get(f"/purchase-orders/{self.source_po.id}/edit")
        body = html.unescape(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertRegex(body, r'<input[^>]*name="bo_number"[^>]*readonly')

    def test_update_rejects_bo_number_change(self):
        self._login(self.procurement_user)
        response = self.client.post(
            f"/purchase-orders/{self.source_po.id}/update",
            data={
                "bo_number": "BO99999",
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "supplier_name": "",
                "total_amount": "500.00",
                "advance_amount": "50.00",
                "due_date": "",
                "description": "محاولة تحديث غير مسموحة",
                "reference_po_number": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("لا يمكن تعديل رقم BO بعد الإنشاء.", response.get_data(as_text=True))
        updated_po = db.session.get(PurchaseOrder, self.source_po.id)
        self.assertEqual(updated_po.bo_number, self.source_po.bo_number)
        self.assertEqual(updated_po.description, self.source_po.description)

    def test_update_allows_same_bo_number(self):
        self._login(self.procurement_user)
        response = self.client.post(
            f"/purchase-orders/{self.source_po.id}/update",
            data={
                "bo_number": self.source_po.bo_number,
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "supplier_name": "",
                "total_amount": "600.00",
                "advance_amount": "50.00",
                "due_date": "",
                "description": "تحديث الوصف",
                "reference_po_number": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("تم تحديث أمر الشراء بنجاح.", response.get_data(as_text=True))
        updated_po = db.session.get(PurchaseOrder, self.source_po.id)
        self.assertEqual(updated_po.bo_number, self.source_po.bo_number)
        self.assertEqual(updated_po.description, "تحديث الوصف")

    def test_update_allows_missing_bo_number_field(self):
        self._login(self.procurement_user)
        response = self.client.post(
            f"/purchase-orders/{self.source_po.id}/update",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "supplier_name": "",
                "total_amount": "650.00",
                "advance_amount": "50.00",
                "due_date": "",
                "description": "تحديث بدون رقم BO",
                "reference_po_number": "",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("تم تحديث أمر الشراء بنجاح.", response.get_data(as_text=True))
        updated_po = db.session.get(PurchaseOrder, self.source_po.id)
        self.assertEqual(updated_po.bo_number, self.source_po.bo_number)
        self.assertEqual(updated_po.description, "تحديث بدون رقم BO")


if __name__ == "__main__":
    unittest.main()
