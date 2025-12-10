# blueprints/users/__init__.py

from flask import Blueprint

users_bp = Blueprint(
    "users",
    __name__,
    template_folder="../../templates/users"
)

from . import routes  # noqa
