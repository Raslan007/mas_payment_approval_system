# models.py

from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


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
    project = db.relationship("Project", backref="users")

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
