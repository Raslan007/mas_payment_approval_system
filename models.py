# models.py

from datetime import datetime
import logging

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text

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

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    supplier_type = db.Column(db.String(50), nullable=False)  # مقاول / مورد مواد / ...

    def __repr__(self):
        return f"<Supplier {self.name}>"


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

    request_type = db.Column(db.String(50), nullable=False)  # مقاول / مشتريات / عهدة
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=True)

    # مبلغ المالية الفعلي (المبلغ الذي تم اعتماده للصرف من الإدارة المالية)
    amount_finance = db.Column(db.Float, nullable=True)

    # نسبة الإنجاز وقت الدفعة (0–100)
    progress_percentage = db.Column(db.Float, nullable=True)

    status = db.Column(db.String(50), default="draft", nullable=False)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_to_pm_at = db.Column(db.DateTime, nullable=True)

    project = db.relationship("Project", backref="payment_requests")
    supplier = db.relationship("Supplier", backref="payment_requests")
    creator = db.relationship("User", backref="created_requests", foreign_keys=[created_by])

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
    def finance_diff(self) -> float | None:
        """
        الفرق = مبلغ المالية - مبلغ المهندس
        موجب  => المالية صرفت أكثر من المطلوب
        سالب  => المالية صرفت أقل من المطلوب
        صفر   => مطابق للمطلوب
        """
        if self.amount_finance is None or self.amount is None:
            return None
        return float(self.amount_finance) - float(self.amount)


PURCHASE_ORDER_REQUEST_TYPE = "مشتريات"


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
