# blueprints/projects/__init__.py

from flask import Blueprint

projects_bp = Blueprint(
    "projects",
    __name__,
    template_folder="../../templates/projects"
)

from . import routes  # noqa
