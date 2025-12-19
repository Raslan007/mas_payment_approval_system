import os
from datetime import timedelta


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
    _env_secret = os.environ.get("SECRET_KEY")
    _is_production = (
        os.environ.get("APP_ENV") == "production"
        or os.environ.get("FLASK_ENV") == "production"
    )

    if _is_production and (not _env_secret or _env_secret == "secret-key-change-me"):
        raise RuntimeError(
            "SECRET_KEY must be set to a secure, non-default value when running in production."
        )

    SECRET_KEY = _env_secret or "secret-key-change-me"

    _database_url = os.environ.get("DATABASE_URL")
    if _database_url and _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _database_url or "sqlite:///" + os.path.join(BASE_DIR, "payments.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DEBUG = _get_bool_env("FLASK_DEBUG", default=False)
    AUTO_SCHEMA_BOOTSTRAP = _get_bool_env("AUTO_SCHEMA_BOOTSTRAP", default=False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = bool(_is_production)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = bool(_is_production)
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
