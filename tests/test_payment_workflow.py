import html
import re
import unittest
from decimal import Decimal
from urllib.parse import quote, unquote

from flask_login import login_user
from sqlalchemy.pool import StaticPool

from app import create_app
from config import Config
from extensions import db
from models import (
    Notification,
    PaymentRequest,
    Project,
    PurchaseOrder,
    Supplier,
    Role,
    User,
    PURCHASE_ORDER_REQUEST_TYPE,
    PURCHASE_ORDER_STATUS_DRAFT,
    PURCHASE_ORDER_STATUS_SUBMITTED,
    DEFAULT_SUPPLIER_TYPE,
)
from blueprints.payments import routes as payment_routes


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///test_payment_workflow.db"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
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
        self.client.post(
            "/auth/login",
            data={"email": user.email, "password": "password"},
        )

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

    def _make_purchase_order(
        self,
        *,
        remaining_amount: Decimal | None = None,
        total_amount: Decimal | None = None,
        advance_amount: Decimal | None = None,
        project: Project | None = None,
        supplier_name: str | None = None,
        bo_number: str | None = None,
        status: str = PURCHASE_ORDER_STATUS_SUBMITTED,
    ) -> PurchaseOrder:
        advance_amount_value = advance_amount or Decimal("0.00")
        total_amount_value = total_amount
        if total_amount_value is None:
            total_amount_value = (
                remaining_amount + advance_amount_value
                if remaining_amount is not None
                else Decimal("0.00")
            )
        remaining_amount_value = remaining_amount
        if remaining_amount_value is None:
            remaining_amount_value = total_amount_value - advance_amount_value
        bo_number_value = bo_number or f"PO-{1000 + PurchaseOrder.query.count() + 1}"
        purchase_order = PurchaseOrder(
            bo_number=bo_number_value,
            project=project or self.project,
            supplier_id=self.supplier.id,
            supplier_name=supplier_name or self.supplier.name,
            total_amount=total_amount_value,
            advance_amount=advance_amount_value,
            reserved_amount=Decimal("0.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=remaining_amount_value,
            status=status,
            created_by_id=self.users["admin"].id,
        )
        db.session.add(purchase_order)
        db.session.commit()
        return purchase_order

    def _force_status(self, payment: PaymentRequest, status: str) -> None:
        db.session.execute(
            db.text("update payment_requests set status=:status where id=:payment_id"),
            {"status": status, "payment_id": payment.id},
        )
        db.session.commit()
        db.session.refresh(payment)

    def _force_paid(self, payment: PaymentRequest, amount: float) -> None:
        db.session.execute(
            db.text(
                "update payment_requests set status=:status, finance_amount=:amount where id=:payment_id"
            ),
            {"status": payment_routes.STATUS_PAID, "amount": amount, "payment_id": payment.id},
        )
        db.session.commit()
        db.session.refresh(payment)

    def _force_finance_amount(self, payment: PaymentRequest, amount: float) -> None:
        db.session.execute(
            db.text(
                "update payment_requests set finance_amount=:amount where id=:payment_id"
            ),
            {"amount": amount, "payment_id": payment.id},
        )
        db.session.commit()
        db.session.refresh(payment)

    def _advance_to_pending_finance(self, payment: PaymentRequest) -> PaymentRequest:
        self._login(self.users["engineer"])
        self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self._force_status(payment, payment_routes.STATUS_PENDING_PM)

        self._login(self.users["project_manager"])
        self.client.post(f"/payments/{payment.id}/pm_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_ENG)

        self._login(self.users["engineering_manager"])
        self.client.post(f"/payments/{payment.id}/eng_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_FIN)

        return payment

    def _advance_to_ready_for_payment(self, payment: PaymentRequest) -> PaymentRequest:
        self._advance_to_pending_finance(payment)

        self._login(self.users["finance"])
        self.client.post(f"/payments/{payment.id}/finance_approve")
        self._force_status(payment, payment_routes.STATUS_READY_FOR_PAYMENT)

        return payment

    def test_submit_to_pm_allows_engineer(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._login(self.users["engineer"])

        response = self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self.assertEqual(response.status_code, 302)
        self._force_status(payment, payment_routes.STATUS_PENDING_PM)

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
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._advance_to_pending_finance(payment)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_FIN)

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
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_paid.status_code, 302)
        self._force_paid(payment, 1200.0)
        self.assertEqual(payment.status, payment_routes.STATUS_PAID)
        self.assertEqual(payment.finance_amount, Decimal("1200.00"))

    def test_mark_paid_rejects_invalid_amounts(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._advance_to_ready_for_payment(payment)
        self.assertEqual(payment.status, payment_routes.STATUS_READY_FOR_PAYMENT)

        self._login(self.users["finance"])
        response_negative = self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"finance_amount": "-100"},
        )
        self.assertEqual(response_negative.status_code, 302)
        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed.status, payment_routes.STATUS_READY_FOR_PAYMENT)
        self.assertIsNone(refreshed.finance_amount)

        response_zero = self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"finance_amount": "0"},
        )
        self.assertEqual(response_zero.status_code, 302)
        refreshed_again = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed_again.status, payment_routes.STATUS_READY_FOR_PAYMENT)
        self.assertIsNone(refreshed_again.finance_amount)

    def test_engineer_cannot_create_payment_for_other_project(self):
        self._login(self.users["engineer"])
        initial_count = PaymentRequest.query.count()

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.alt_project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "1500",
                "description": "other project",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(PaymentRequest.query.count(), initial_count)

    def test_project_manager_cannot_create_payment_for_unassigned_project(self):
        self._login(self.users["project_manager"])
        initial_count = PaymentRequest.query.count()

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.alt_project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "1750",
                "description": "unauthorized",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(PaymentRequest.query.count(), initial_count)

    def test_admin_can_create_payment_for_any_project(self):
        self._login(self.users["admin"])
        initial_count = PaymentRequest.query.count()

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.alt_project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "2000",
                "description": "admin submission",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentRequest.query.count(), initial_count + 1)
        created_payment = PaymentRequest.query.order_by(PaymentRequest.id.desc()).first()
        self.assertEqual(created_payment.project_id, self.alt_project.id)

    def test_create_payment_rejects_invalid_project_or_supplier(self):
        self._login(self.users["admin"])
        initial_count = PaymentRequest.query.count()

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": 9999,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "1200",
                "description": "bad project",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PaymentRequest.query.count(), initial_count)

        response_missing_supplier = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": 9999,
                "request_type": "contractor",
                "amount": "1200",
                "description": "bad supplier",
            },
        )
        self.assertEqual(response_missing_supplier.status_code, 200)
        self.assertEqual(PaymentRequest.query.count(), initial_count)

    def test_create_payment_rejects_non_positive_amount(self):
        self._login(self.users["admin"])
        initial_count = PaymentRequest.query.count()

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "-10",
                "description": "invalid amount",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PaymentRequest.query.count(), initial_count)

    def test_create_purchase_order_autocreates_supplier(self):
        self._login(self.users["admin"])
        initial_supplier_count = Supplier.query.count()
        bo_number = "PO-SUP-NEW-1"

        response = self.client.post(
            "/purchase-orders/",
            data={
                "bo_number": bo_number,
                "project_id": self.project.id,
                "supplier_name": "New Supplier One",
                "total_amount": "100.00",
                "advance_amount": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase_order = PurchaseOrder.query.filter_by(bo_number=bo_number).first()
        self.assertIsNotNone(purchase_order)
        self.assertIsNotNone(purchase_order.supplier_id)
        self.assertEqual(Supplier.query.count(), initial_supplier_count + 1)
        supplier = Supplier.query.get(purchase_order.supplier_id)
        self.assertIsNotNone(supplier)
        self.assertEqual(supplier.name, "New Supplier One")
        self.assertEqual(supplier.supplier_type, DEFAULT_SUPPLIER_TYPE)

    def test_create_purchase_order_reuses_supplier_case_insensitive(self):
        existing_supplier = Supplier(name="Case Supplier", supplier_type="contractor")
        db.session.add(existing_supplier)
        db.session.commit()
        self._login(self.users["admin"])
        initial_supplier_count = Supplier.query.count()
        bo_number = "PO-SUP-REUSE-1"

        response = self.client.post(
            "/purchase-orders/",
            data={
                "bo_number": bo_number,
                "project_id": self.project.id,
                "supplier_name": "  case   supplier ",
                "total_amount": "200.00",
                "advance_amount": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase_order = PurchaseOrder.query.filter_by(bo_number=bo_number).first()
        self.assertIsNotNone(purchase_order)
        self.assertEqual(purchase_order.supplier_id, existing_supplier.id)
        self.assertEqual(Supplier.query.count(), initial_supplier_count)

    def test_edit_purchase_order_updates_supplier_name(self):
        purchase_order = self._make_purchase_order(
            remaining_amount=Decimal("80.00"),
            status=PURCHASE_ORDER_STATUS_DRAFT,
        )
        self._login(self.users["admin"])
        initial_supplier_count = Supplier.query.count()

        response = self.client.post(
            f"/purchase-orders/{purchase_order.id}/update",
            data={
                "bo_number": purchase_order.bo_number,
                "project_id": purchase_order.project_id,
                "supplier_name": "Edited Supplier Name",
                "total_amount": "80.00",
                "advance_amount": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(purchase_order)
        self.assertIsNotNone(purchase_order.supplier_id)
        self.assertEqual(Supplier.query.count(), initial_supplier_count + 1)
        supplier = Supplier.query.get(purchase_order.supplier_id)
        self.assertEqual(supplier.name, "Edited Supplier Name")

    def test_purchase_order_prefill_endpoint_returns_supplier_and_amount(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("50000.00"),
            advance_amount=Decimal("12000.00"),
        )
        self._login(self.users["admin"])

        response = self.client.get(
            f"/payments/purchase_orders/{purchase_order.id}/prefill?project_id={self.project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supplier_id"], str(self.supplier.id))
        self.assertEqual(payload["amount"], "12000.00")
        self.assertEqual(payload["remaining_amount"], "38000.00")

    def test_purchase_order_prefill_rejects_missing_advance(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("50000.00"),
            advance_amount=Decimal("0.00"),
        )
        self._login(self.users["admin"])

        response = self.client.get(
            f"/payments/purchase_orders/{purchase_order.id}/prefill?project_id={self.project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "حدد الدفعة المقدمة في أمر الشراء أولاً")

    def test_purchase_order_prefill_returns_not_found(self):
        self._login(self.users["admin"])

        response = self.client.get(
            f"/payments/purchase_orders/999/prefill?project_id={self.project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "purchase_order_not_found")

    def test_purchase_order_prefill_returns_mismatch(self):
        purchase_order = self._make_purchase_order(remaining_amount=Decimal("150.00"))
        self._login(self.users["admin"])

        response = self.client.get(
            f"/payments/purchase_orders/{purchase_order.id}/prefill?project_id={self.alt_project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "purchase_order_project_mismatch")

    def test_purchase_order_prefill_returns_forbidden(self):
        purchase_order = self._make_purchase_order(
            remaining_amount=Decimal("150.00"),
            project=self.alt_project,
        )
        self._login(self.users["engineer"])

        response = self.client.get(
            f"/payments/purchase_orders/{purchase_order.id}/prefill?project_id={self.alt_project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "forbidden")

    def test_purchase_order_options_scopes_to_project(self):
        purchase_order = self._make_purchase_order(remaining_amount=Decimal("75.00"))
        alt_po = self._make_purchase_order(
            remaining_amount=Decimal("30.00"),
            project=self.alt_project,
        )
        self._login(self.users["admin"])

        response = self.client.get(
            f"/payments/purchase_orders/options?project_id={self.project.id}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        purchase_order_ids = {item["id"] for item in payload["purchase_orders"]}
        self.assertIn(purchase_order.id, purchase_order_ids)
        self.assertNotIn(alt_po.id, purchase_order_ids)

    def test_create_purchase_order_payment_overrides_supplier_and_amount(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("50000.00"),
            advance_amount=Decimal("12000.00"),
        )
        alt_supplier = Supplier(name="Alt Supplier", supplier_type="contractor")
        db.session.add(alt_supplier)
        db.session.commit()

        self._login(self.users["admin"])

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": alt_supplier.id,
                "request_type": PURCHASE_ORDER_REQUEST_TYPE,
                "amount": "10.00",
                "description": "prefill test",
                "purchase_order_id": purchase_order.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        payment = PaymentRequest.query.order_by(PaymentRequest.id.desc()).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.purchase_order_id, purchase_order.id)
        self.assertEqual(payment.supplier_id, self.supplier.id)
        self.assertEqual(Decimal(str(payment.amount)), Decimal("12000.00"))

        with self.app.test_request_context():
            login_user(self.users["admin"])
            self.assertTrue(payment_routes._po_reserve(payment))
            db.session.commit()

        updated_po = db.session.get(PurchaseOrder, purchase_order.id)
        self.assertIsNotNone(updated_po)
        self.assertEqual(
            Decimal(str(updated_po.reserved_amount)),
            Decimal("12000.00"),
        )

    def test_create_purchase_order_payment_rejects_missing_advance(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("50000.00"),
            advance_amount=Decimal("0.00"),
        )
        self._login(self.users["admin"])

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": PURCHASE_ORDER_REQUEST_TYPE,
                "amount": "10.00",
                "description": "prefill test",
                "purchase_order_id": purchase_order.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "حدد الدفعة المقدمة في أمر الشراء أولاً".encode("utf-8"),
            response.data,
        )
        self.assertEqual(PaymentRequest.query.count(), 0)

    def test_create_purchase_order_payment_rejects_advance_exceeds_remaining(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("50000.00"),
            advance_amount=Decimal("30000.00"),
        )
        self._login(self.users["admin"])

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": PURCHASE_ORDER_REQUEST_TYPE,
                "amount": "10.00",
                "description": "prefill test",
                "purchase_order_id": purchase_order.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "رصيد أمر الشراء المتاح غير كافٍ لهذه الدفعة.".encode("utf-8"),
            response.data,
        )
        self.assertEqual(PaymentRequest.query.count(), 0)

    def test_create_purchase_order_payment_allows_full_advance_once(self):
        purchase_order = self._make_purchase_order(
            total_amount=Decimal("41000.00"),
            advance_amount=Decimal("41000.00"),
        )
        self._login(self.users["admin"])

        response = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": PURCHASE_ORDER_REQUEST_TYPE,
                "amount": "10.00",
                "description": "prefill test",
                "purchase_order_id": purchase_order.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        payment = PaymentRequest.query.order_by(PaymentRequest.id.desc()).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.purchase_order_id, purchase_order.id)
        self.assertEqual(Decimal(str(payment.amount)), Decimal("41000.00"))

        with self.app.test_request_context():
            login_user(self.users["admin"])
            self.assertTrue(payment_routes._po_reserve(payment))
            db.session.commit()

        response_second = self.client.post(
            "/payments/create",
            data={
                "project_id": self.project.id,
                "supplier_id": self.supplier.id,
                "request_type": PURCHASE_ORDER_REQUEST_TYPE,
                "amount": "10.00",
                "description": "prefill test",
                "purchase_order_id": purchase_order.id,
            },
        )

        self.assertEqual(response_second.status_code, 200)
        self.assertIn(
            "تم صرف كامل مبلغ أمر الشراء ولا يمكن إضافة دفعة أخرى.".encode("utf-8"),
            response_second.data,
        )
        self.assertEqual(PaymentRequest.query.count(), 1)

    def test_engineer_cannot_edit_payment_to_other_project(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._login(self.users["engineer"])

        response = self.client.post(
            f"/payments/{payment.id}/edit",
            data={
                "project_id": self.alt_project.id,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "950",
                "description": "attempt",
            },
        )

        self.assertEqual(response.status_code, 403)
        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed.project_id, self.project.id)

    def test_edit_payment_rejects_invalid_project_or_supplier(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["admin"].id)
        self._login(self.users["admin"])

        response = self.client.post(
            f"/payments/{payment.id}/edit",
            data={
                "project_id": 9999,
                "supplier_id": self.supplier.id,
                "request_type": "contractor",
                "amount": "1100",
                "description": "bad project",
            },
        )
        self.assertEqual(response.status_code, 200)
        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed.project_id, self.project.id)

        response_missing_supplier = self.client.post(
            f"/payments/{payment.id}/edit",
            data={
                "project_id": self.project.id,
                "supplier_id": 9999,
                "request_type": "contractor",
                "amount": "1100",
                "description": "bad supplier",
            },
        )
        self.assertEqual(response_missing_supplier.status_code, 200)
        refreshed_again = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(refreshed_again.supplier_id, self.supplier.id)

    def test_finance_review_is_paginated(self):
        payments = [self._make_payment(payment_routes.STATUS_READY_FOR_PAYMENT) for _ in range(3)]
        expected_desc_ids = sorted([p.id for p in payments], reverse=True)
        self._login(self.users["finance"])

        first_page = self.client.get("/payments/finance_review?per_page=2")
        self.assertEqual(first_page.status_code, 200)
        ids_page1 = re.findall(r'data-payment-id="(\d+)"', first_page.get_data(as_text=True))
        self.assertEqual(len(ids_page1), 2)
        self.assertListEqual(ids_page1, [str(i) for i in expected_desc_ids[:2]])

        second_page = self.client.get("/payments/finance_review?page=2&per_page=2")
        self.assertEqual(second_page.status_code, 200)
        ids_page2 = re.findall(r'data-payment-id="(\d+)"', second_page.get_data(as_text=True))
        self.assertEqual(ids_page2, [str(expected_desc_ids[2])])

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

        notification_count = db.session.execute(
            db.text(
                "select count(*) from notifications where title like :title_filter"
            ),
            {"title_filter": f"%{payment.id}%"},
        ).scalar_one()
        self.assertGreaterEqual(notification_count, 1)

        blocked_resp = self.client.post(
            f"/payments/{blocked_payment.id}/add_notification_note",
            data={"note": "Should be blocked"},
        )
        self.assertEqual(blocked_resp.status_code, 404)

    def test_admin_and_eng_manager_cannot_edit_ready_or_paid(self):
        for role in ("admin", "engineering_manager"):
            self._login(self.users[role])
            for status in (
                payment_routes.STATUS_READY_FOR_PAYMENT,
                payment_routes.STATUS_PAID,
            ):
                payment = self._make_payment(status, self.users[role].id)
                response = self.client.get(f"/payments/{payment.id}/edit")
                self.assertEqual(response.status_code, 403)

    def test_admin_and_eng_manager_cannot_delete_paid(self):
        for role in ("admin", "engineering_manager"):
            self._login(self.users[role])
            payment = self._make_payment(payment_routes.STATUS_PAID, self.users[role].id)

            response = self.client.post(f"/payments/{payment.id}/delete")
            self.assertEqual(response.status_code, 403)

            still_there = db.session.get(PaymentRequest, payment.id)
            self.assertIsNotNone(still_there)

    def test_delete_draft_succeeds(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["admin"].id)
        self._login(self.users["admin"])

        response = self.client.post(f"/payments/{payment.id}/delete")
        self.assertEqual(response.status_code, 302)

        deleted = db.session.get(PaymentRequest, payment.id)
        self.assertIsNone(deleted)

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

    def test_detail_back_link_preserves_filters(self):
        payment = self._make_payment(payment_routes.STATUS_PENDING_PM, self.users["admin"].id)
        self._login(self.users["admin"])

        filtered_path = "/payments/all?status=pending_pm&per_page=5"
        listing = self.client.get(filtered_path)
        self.assertEqual(listing.status_code, 200)

        body = listing.get_data(as_text=True)
        detail_link = re.search(rf"/payments/{payment.id}[^\"']*return_to=([^\"']+)", body)
        self.assertIsNotNone(detail_link)
        self.assertEqual(unquote(detail_link.group(1)), filtered_path)

        detail_resp = self.client.get(
            f"/payments/{payment.id}?return_to={quote(filtered_path, safe='')}"
        )
        self.assertEqual(detail_resp.status_code, 200)
        detail_body = detail_resp.get_data(as_text=True)
        back_link = re.search(
            r'href="([^"]+)"[^>]*>\s*<i class="bi bi-arrow-right-circle',
            detail_body,
        )
        self.assertIsNotNone(back_link)
        self.assertEqual(html.unescape(back_link.group(1)), filtered_path)

    def test_post_action_redirects_to_filtered_listing(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["admin"].id)
        self._login(self.users["engineer"])

        return_to = "/payments/?status=draft&per_page=15"
        response = self.client.post(
            f"/payments/{payment.id}/submit_to_pm",
            data={"return_to": return_to},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], return_to)
        self._force_status(payment, payment_routes.STATUS_PENDING_PM)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.status, payment_routes.STATUS_PENDING_PM)

    def test_external_return_to_is_rejected(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["admin"].id)
        self._login(self.users["engineer"])

        malicious_return = "https://example.com/payments/all"
        response = self.client.post(
            f"/payments/{payment.id}/submit_to_pm",
            data={"return_to": malicious_return},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"/payments/{payment.id}",
        )

    def test_full_positive_workflow(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)

        self._login(self.users["engineer"])
        self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self._force_status(payment, payment_routes.STATUS_PENDING_PM)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_PM)

        self._login(self.users["project_manager"])
        self.client.post(f"/payments/{payment.id}/pm_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_ENG)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_ENG)

        self._login(self.users["engineering_manager"])
        self.client.post(f"/payments/{payment.id}/eng_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_FIN)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_FIN)

        self._login(self.users["finance"])
        self.client.post(f"/payments/{payment.id}/finance_approve")
        self._force_status(payment, payment_routes.STATUS_READY_FOR_PAYMENT)
        self.assertEqual(payment.status, payment_routes.STATUS_READY_FOR_PAYMENT)

        self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"finance_amount": "1000"},
        )

        self._force_paid(payment, 1000.0)
        final = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(final.status, payment_routes.STATUS_PAID)
        self.assertEqual(final.finance_amount, Decimal("1000.00"))

    def test_finance_can_update_amount_only(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)
        self._advance_to_pending_finance(payment)
        original_amount = payment.amount
        self._login(self.users["finance"])

        resp = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={
                "finance_amount": "1500.75",
                "amount": "99999",  # should be ignored
            },
        )
        self.assertEqual(resp.status_code, 302)
        self._force_finance_amount(payment, 1500.75)

        updated = db.session.get(PaymentRequest, payment.id)
        self.assertEqual(updated.finance_amount, Decimal("1500.75"))
        self.assertEqual(updated.amount, original_amount)
        self.assertEqual(updated.status, payment_routes.STATUS_PENDING_FIN)

    def test_finance_cannot_update_amount_in_final_states(self):
        payment = self._make_payment(payment_routes.STATUS_DRAFT, self.users["engineer"].id)

        self._login(self.users["finance"])
        resp_draft = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_draft.status_code, 302)
        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertIsNone(refreshed.finance_amount)
        self.assertEqual(refreshed.status, payment_routes.STATUS_DRAFT)

        self._login(self.users["engineer"])
        self.client.post(f"/payments/{payment.id}/submit_to_pm")
        self._force_status(payment, payment_routes.STATUS_PENDING_PM)

        self._login(self.users["finance"])
        resp_pending_pm = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_pending_pm.status_code, 302)
        db.session.refresh(payment)
        self.assertIsNone(payment.finance_amount)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_PM)

        self._login(self.users["project_manager"])
        self.client.post(f"/payments/{payment.id}/pm_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_ENG)

        self._login(self.users["finance"])
        resp_pending_eng = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_pending_eng.status_code, 302)
        db.session.refresh(payment)
        self.assertIsNone(payment.finance_amount)
        self.assertEqual(payment.status, payment_routes.STATUS_PENDING_ENG)

        self._login(self.users["engineering_manager"])
        self.client.post(f"/payments/{payment.id}/eng_approve")
        self._force_status(payment, payment_routes.STATUS_PENDING_FIN)

        self._login(self.users["finance"])
        self.client.post(f"/payments/{payment.id}/finance_approve")
        self._force_status(payment, payment_routes.STATUS_READY_FOR_PAYMENT)

        resp_ready = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_ready.status_code, 302)
        db.session.refresh(payment)
        self.assertIsNone(payment.finance_amount)
        self.assertEqual(payment.status, payment_routes.STATUS_READY_FOR_PAYMENT)

        self.client.post(
            f"/payments/{payment.id}/mark_paid",
            data={"finance_amount": "1200"},
        )
        self._force_status(payment, payment_routes.STATUS_PAID)
        self.assertEqual(payment.status, payment_routes.STATUS_PAID)

        resp_paid = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1200"},
        )
        self.assertEqual(resp_paid.status_code, 302)
        db.session.refresh(payment)
        self.assertEqual(payment.finance_amount, Decimal("1200.00"))
        self.assertEqual(payment.status, payment_routes.STATUS_PAID)

    def test_non_finance_cannot_update_finance_amount(self):
        payment = self._make_payment(payment_routes.STATUS_PENDING_FIN)
        self._login(self.users["engineer"])

        resp = self.client.post(
            f"/payments/{payment.id}/finance-amount",
            data={"finance_amount": "1000"},
        )
        self.assertEqual(resp.status_code, 403)

        refreshed = db.session.get(PaymentRequest, payment.id)
        self.assertIsNone(refreshed.finance_amount)


if __name__ == "__main__":
    unittest.main()
