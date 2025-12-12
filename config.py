# config.py

import os


def _get_bool_env(var_name: str, default: bool = False) -> bool:
    """Return a boolean from environment variables.

    Accepts common truthy strings (1/true/yes/on) case-insensitively.
    """
    value = os.environ.get(var_name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "payments.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEBUG = _get_bool_env("FLASK_DEBUG", default=False)
