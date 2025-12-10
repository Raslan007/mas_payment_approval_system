# blueprints/suppliers/__init__.py

from flask import Blueprint

suppliers_bp = Blueprint(
    "suppliers",
    __name__,
    template_folder="../../templates/suppliers"
)

from . import routes  # noqa
