# blueprints/payments/__init__.py

from flask import Blueprint

payments_bp = Blueprint(
    "payments",
    __name__,
    template_folder="../../templates/payments"
)

from . import routes  # noqa
