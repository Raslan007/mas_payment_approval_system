# models.py

from datetime import datetime
from decimal import Decimal
import logging

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text, column
from sqlalchemy import event

from extensions import db

logger = logging.getLogger(__name__)


user_projects = db.Table(
    "user_projects",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("project_id", db.Integer, db.ForeignKey("projects.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    # admin, engineer, project_manager, engineering_manager, finance
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f"<Role {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))
    role = db.relationship("Role", backref="users")

    # المشروع الرئيسي المرتبط بالمستخدم (مهندس / مدير مشروع)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    project = db.relationship("Project", backref="primary_users")
    projects = db.relationship(
        "Project",
        secondary=user_projects,
        backref=db.backref("users", lazy="dynamic"),
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, name: str) -> bool:
        return self.role is not None and self.role.name == name

    def __repr__(self):
        return f"<User {self.full_name}>"


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    project_name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=True)

    def __repr__(self):
        return f"<Project {self.project_name}>"


class Supplier(db.Model):
    __tablename__ = "suppliers"
    __table_args__ = (
        db.Index(
            "ux_suppliers_lower_name",
            db.func.lower(column("name")),
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    supplier_type = db.Column(db.String(50), nullable=False)  # مقاول / مورد مواد / ...

    def __repr__(self):
        return f"<Supplier {self.name}>"


DEFAULT_SUPPLIER_TYPE = "غير محدد"


def normalize_supplier_name(name: str) -> str:
    return " ".join(name.split())


def get_or_create_supplier_by_name(name: str) -> Supplier:
    normalized = normalize_supplier_name(name)
    supplier = Supplier.query.filter(
        db.func.lower(Supplier.name) == normalized.lower()
    ).first()
    if supplier:
        supplier.was_created = False
        return supplier

    supplier = Supplier(name=normalized, supplier_type=DEFAULT_SUPPLIER_TYPE)
    supplier.was_created = True
    db.session.add(supplier)
    db.session.flush()
    return supplier


class PaymentRequest(db.Model):
    """
    طلب دفعة واحد في النظام (مقاول / مشتريات / عهدة)

    الحالات المستخدمة حالياً:
        draft              -> قام المهندس بإدخال الطلب
        pending_pm         -> في انتظار اعتماد مدير المشروع
        pending_eng        -> في انتظار الإدارة الهندسية
        pending_finance    -> في انتظار الإدارة المالية
        ready_for_payment  -> جاهزة للصرف
        paid               -> تم الصرف فعليًا
        rejected           -> مرفوضة

    (مع دعم بعض القيم القديمة لو موجودة في البيانات)
    """
    __tablename__ = "payment_requests"

    id = db.Column(db.Integer, primary_key=True)

    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_order_id = db.Column(
        db.Integer,
        db.ForeignKey("purchase_orders.id"),
        nullable=True,
        index=True,
    )
    purchase_order_reserved_at = db.Column(db.DateTime, nullable=True)
    purchase_order_reserved_amount = db.Column(db.Numeric(14, 2), nullable=True)
    purchase_order_finalized_at = db.Column(db.DateTime, nullable=True)

    request_type = db.Column(db.String(50), nullable=False)  # مقاول / مشتريات / عهدة
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # مبلغ المالية الفعلي (المبلغ الذي تم اعتماده للصرف من الإدارة المالية)
    finance_amount = db.Column(db.Numeric(14, 2), nullable=True)

    # نسبة الإنجاز وقت الدفعة (0–100)
    progress_percentage = db.Column(db.Float, nullable=True)

    status = db.Column(db.String(50), default="draft", nullable=False)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_to_pm_at = db.Column(db.DateTime, nullable=True)

    project = db.relationship("Project", backref="payment_requests")
    supplier = db.relationship("Supplier", backref="payment_requests")
    purchase_order = db.relationship(
        "PurchaseOrder",
        backref=db.backref("payment_requests", lazy="dynamic"),
    )
    creator = db.relationship("User", backref="created_requests", foreign_keys=[created_by])
    finance_adjustments = db.relationship(
        "PaymentFinanceAdjustment",
        back_populates="payment",
        cascade="all, delete-orphan",
        order_by="PaymentFinanceAdjustment.created_at.asc()",
    )

    def __repr__(self):
        return f"<PaymentRequest {self.id} - {self.amount}>"

    @property
    def human_status(self) -> str:
        mapping = {
            # الحالات الحالية
            "draft": "مسودة (بواسطة المهندس)",
            "pending_pm": "في انتظار اعتماد مدير المشروع",
            "pending_eng": "في انتظار الإدارة الهندسية",
            "pending_finance": "في انتظار اعتماد المالية",
            "ready_for_payment": "جاهزة للصرف",
            "paid": "تم الصرف",
            "rejected": "مرفوض",

            # دعم قيم قديمة لو موجودة
            "under_review_pm": "تحت مراجعة مدير المشروع",
            "under_review_eng": "تحت مراجعة الإدارة الهندسية",
            "waiting_finance": "في انتظار اعتماد المالية",
            "approved": "معتمد نهائيًا",
        }
        return mapping.get(self.status, self.status)

    @property
    def status_badge_class(self) -> str:
        mapping = {
            "draft": "secondary",
            "pending_pm": "info",
            "pending_eng": "primary",
            "pending_finance": "warning",
            "ready_for_payment": "info",
            "paid": "success",
            "rejected": "danger",

            # قيم قديمة
            "under_review_pm": "info",
            "under_review_eng": "primary",
            "waiting_finance": "warning",
            "approved": "success",
        }
        return mapping.get(self.status, "secondary")

    @property
    def finance_diff(self) -> Decimal | None:
        """
        الفرق = مبلغ المالية - مبلغ المهندس
        موجب  => المالية صرفت أكثر من المطلوب
        سالب  => المالية صرفت أقل من المطلوب
        صفر   => مطابق للمطلوب
        """
        if self.amount is None or self.finance_amount is None:
            return None
        return self.finance_effective_amount - self.amount_decimal

    @property
    def amount_decimal(self) -> Decimal:
        if self.amount is None:
            return Decimal("0.00")
        return Decimal(str(self.amount))

    @property
    def finance_adjustments_total(self) -> Decimal:
        total = Decimal("0.00")
        for adjustment in self.finance_adjustments:
            if adjustment.is_void:
                continue
            total += adjustment.delta_amount or Decimal("0.00")
        return total

    @property
    def finance_effective_amount(self) -> Decimal:
        base_amount = Decimal("0.00")
        if self.finance_amount is not None:
            base_amount = Decimal(str(self.finance_amount))
        return base_amount + self.finance_adjustments_total


class PaymentFinanceAdjustment(db.Model):
    __tablename__ = "payment_finance_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delta_amount = db.Column(db.Numeric(14, 2), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    is_void = db.Column(db.Boolean, default=False, nullable=False, index=True)
    voided_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    voided_at = db.Column(db.DateTime, nullable=True)
    void_reason = db.Column(db.String(255), nullable=True)

    payment = db.relationship("PaymentRequest", back_populates="finance_adjustments")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    voided_by = db.relationship("User", foreign_keys=[voided_by_user_id])

    def __repr__(self) -> str:  # type: ignore
        return f"<PaymentFinanceAdjustment {self.id} for payment {self.payment_id}>"


PURCHASE_ORDER_REQUEST_TYPE = "مشتريات"

PURCHASE_ORDER_STATUS_DRAFT = "draft"
PURCHASE_ORDER_STATUS_SUBMITTED = "submitted"
PURCHASE_ORDER_STATUS_PM_APPROVED = "pm_approved"
PURCHASE_ORDER_STATUS_ENG_APPROVED = "eng_approved"
PURCHASE_ORDER_STATUS_FINANCE_APPROVED = "finance_approved"
PURCHASE_ORDER_STATUS_CLOSED = "closed"
PURCHASE_ORDER_STATUS_REJECTED = "rejected"


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        db.Index(
            "uq_purchase_orders_bo_number_ci",
            db.func.lower(column("bo_number")),
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    bo_number = db.Column(db.String(50), nullable=False)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    supplier_id = db.Column(
        db.Integer,
        db.ForeignKey("suppliers.id"),
        nullable=False,
        index=True,
    )
    supplier_name = db.Column(db.String(255), nullable=False)
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    advance_amount = db.Column(
        db.Numeric(14, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    reserved_amount = db.Column(
        db.Numeric(14, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    paid_amount = db.Column(
        db.Numeric(14, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    remaining_amount = db.Column(db.Numeric(14, 2), nullable=False)
    status = db.Column(
        db.String(30),
        nullable=False,
        default=PURCHASE_ORDER_STATUS_DRAFT,
    )
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    project = db.relationship("Project", backref="purchase_orders")
    supplier = db.relationship("Supplier", backref="purchase_orders")
    created_by = db.relationship("User", backref="purchase_orders")

    def recalculate_remaining_amount(self) -> None:
        if self.total_amount is None:
            return
        total = Decimal(str(self.total_amount))
        advance = Decimal(str(self.advance_amount or Decimal("0.00")))
        reserved = Decimal(str(self.reserved_amount or Decimal("0.00")))
        paid = Decimal(str(self.paid_amount or Decimal("0.00")))
        self.remaining_amount = total - advance - reserved - paid

    def validate_amounts(self) -> None:
        amounts = {
            "total_amount": self.total_amount,
            "advance_amount": self.advance_amount,
            "reserved_amount": self.reserved_amount,
            "paid_amount": self.paid_amount,
            "remaining_amount": self.remaining_amount,
        }
        for name, value in amounts.items():
            if value is None:
                continue
            if Decimal(str(value)) < 0:
                raise ValueError(f"{name} cannot be negative.")

        if (
            self.total_amount is not None
            and self.advance_amount is not None
            and Decimal(str(self.advance_amount)) > Decimal(str(self.total_amount))
        ):
            raise ValueError("advance_amount cannot exceed total_amount.")

    def __repr__(self) -> str:  # type: ignore
        return f"<PurchaseOrder {self.id} - {self.bo_number}>"


class PurchaseOrderDecision(db.Model):
    __tablename__ = "purchase_order_decisions"

    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(
        db.Integer,
        db.ForeignKey("purchase_orders.id"),
        nullable=False,
        index=True,
    )
    action = db.Column(db.String(20), nullable=False)
    from_status = db.Column(db.String(30), nullable=False)
    to_status = db.Column(db.String(30), nullable=False)
    comment = db.Column(db.Text, nullable=True)
    decided_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    decided_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    purchase_order = db.relationship(
        "PurchaseOrder",
        backref=db.backref(
            "decisions",
            cascade="all, delete-orphan",
            order_by="PurchaseOrderDecision.decided_at.asc()",
        ),
    )
    decided_by = db.relationship("User")

    def __repr__(self) -> str:  # type: ignore
        return f"<PurchaseOrderDecision {self.id} for PO {self.purchase_order_id}>"


@event.listens_for(PurchaseOrder, "before_insert")
@event.listens_for(PurchaseOrder, "before_update")
def _purchase_order_before_save(mapper, connection, target: PurchaseOrder) -> None:
    target.recalculate_remaining_amount()
    target.validate_amounts()


def _payment_request_requires_purchase_order(target: PaymentRequest) -> bool:
    return target.request_type == PURCHASE_ORDER_REQUEST_TYPE


def _payment_request_submitted_from_draft(target: PaymentRequest) -> bool:
    if target.status == "draft":
        return False
    state = inspect(target)
    if state.transient or state.pending:
        return True
    history = state.attrs.status.history
    if history.has_changes() and "draft" in history.deleted:
        return True
    return False


class PaymentNotificationNote(db.Model):
    """
    ملاحظات إشعار المقاولين دون تغيير حالة الدفعة.
    تستخدم بواسطة دور payment_notifier لتسجيل أنه تم التواصل مع المقاول.
    """

    __tablename__ = "payment_notification_notes"

    id = db.Column(db.Integer, primary_key=True)
    payment_request_id = db.Column(
        db.Integer, db.ForeignKey("payment_requests.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    payment_request = db.relationship(
        "PaymentRequest",
        backref=db.backref(
            "notification_notes",
            cascade="all, delete-orphan",
            order_by="desc(PaymentNotificationNote.created_at)",
        ),
    )
    user = db.relationship("User")

    def __repr__(self):  # type: ignore
        return f"<PaymentNotificationNote {self.id} for PR {self.payment_request_id}>"


class PaymentApproval(db.Model):
    """
    سجل حركة الاعتماد لكل طلب دفعة
    """
    __tablename__ = "payment_approvals"

    id = db.Column(db.Integer, primary_key=True)

    payment_request_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_requests.id"),
        nullable=False
    )

    step = db.Column(db.String(50), nullable=False)     # engineer, pm, eng_manager, finance
    action = db.Column(db.String(50), nullable=False)   # submit, approve, reject, mark_paid, etc.
    old_status = db.Column(db.String(50), nullable=True)
    new_status = db.Column(db.String(50), nullable=True)

    comment = db.Column(db.Text, nullable=True)

    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, default=datetime.utcnow)

    payment_request = db.relationship("PaymentRequest", backref="approvals")
    decided_by = db.relationship("User", backref="payment_approvals")

    def __repr__(self):
        return f"<PaymentApproval {self.id} for PR {self.payment_request_id}>"


class PaymentAttachment(db.Model):
    """
    مرفقات الدفعات (فواتير، مستندات، مستخلصات، إلخ)
    """
    __tablename__ = "payment_attachments"

    id = db.Column(db.Integer, primary_key=True)

    payment_request_id = db.Column(
        db.Integer,
        db.ForeignKey("payment_requests.id"),
        nullable=False
    )

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=True)

    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    payment_request = db.relationship("PaymentRequest", backref="attachments")
    uploaded_by = db.relationship("User")

    def __repr__(self):
        return f"<PaymentAttachment {self.id} for PR {self.payment_request_id}>"


class SavedView(db.Model):
    """
    عروض محفوظة خاصة بكل مستخدم لتخزين فلاتر قوائم الدفعات.
    """

    __tablename__ = "saved_views"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    endpoint = db.Column(db.String(255), nullable=False)
    query_string = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("saved_views", cascade="all, delete-orphan"),
    )

    def __repr__(self):  # type: ignore
        return f"<SavedView {self.id} for user {self.user_id}>"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(255), nullable=True)

    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))

    def __repr__(self) -> str:  # type: ignore
        return f"<Notification {self.id} to user {self.user_id}>"


REQUIRED_ROLES: tuple[tuple[str, str], ...] = (
    ("admin", "مدير النظام"),
    ("engineering_manager", "مدير الإدارة الهندسية"),
    ("planning", "مهندس تخطيط"),
    ("project_manager", "مدير مشروع"),
    ("project_engineer", "مهندس مشروع"),
    ("engineer", "مهندس موقع"),
    ("finance", "المالية"),
    ("chairman", "رئيس مجلس الإدارة"),
    ("dc", "Data Entry / Data Control"),
    ("payment_notifier", "مسؤول إشعار المقاولين"),
    ("procurement", "مسؤول المشتريات"),
)


def ensure_roles() -> None:
    """Create any missing roles in an idempotent manner.

    The function is safe to run multiple times and returns quietly when the
    ``roles`` table has not yet been created (e.g., before migrations in tests).
    """

    inspector = inspect(db.engine)
    if not inspector.has_table("roles"):
        return

    existing_roles = {
        name
        for (name,) in db.session.execute(db.select(Role.name)).all()
    }

    roles_to_add: list[Role] = []
    for name, description in REQUIRED_ROLES:
        if name in existing_roles:
            continue

        role = Role(name=name)
        if hasattr(role, "description"):
            role.description = description
        roles_to_add.append(role)

    if roles_to_add:
        db.session.add_all(roles_to_add)
        db.session.commit()


def ensure_schema() -> None:
    """Create any missing database tables without destructive changes.

    SQLAlchemy's ``create_all`` uses ``checkfirst=True`` internally, so running
    this function on startup will create new tables (e.g.,
    ``payment_notification_notes``) if they do not exist while leaving existing
    schema untouched. The function is safe to call multiple times.
    """
    db.create_all()
    ensure_user_project_id_column()


def ensure_user_project_id_column() -> None:
    """Ensure the users.project_id column exists for legacy databases."""

    inspector = inspect(db.engine)
    if not inspector.has_table("users"):
        logger.info("users table missing; skipping users.project_id patch.")
        return

    column_names = {column["name"] for column in inspector.get_columns("users")}
    if "project_id" in column_names:
        logger.info("users.project_id column already present; no patch needed.")
        return

    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        statement = text("ALTER TABLE users ADD COLUMN project_id INTEGER")
        description = "SQLite"
    elif dialect == "postgresql":
        statement = text("ALTER TABLE users ADD COLUMN IF NOT EXISTS project_id INTEGER")
        description = "Postgres"
    else:
        logger.warning(
            "Unsupported dialect '%s' for users.project_id patch; skipping.",
            dialect,
        )
        return

    db.session.execute(statement)
    db.session.commit()
    logger.info("Added users.project_id column using %s patch.", description)
