# blueprints/finance/__init__.py

from flask import Blueprint

finance_bp = Blueprint(
    "finance",
    __name__,
    url_prefix="/finance",
    template_folder="../../templates/finance",
)

from . import routes  # noqa
