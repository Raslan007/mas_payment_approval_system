import json
import logging
import os
from datetime import datetime
from typing import Any

from flask import has_request_context, request, g
from flask_login import current_user


class RequestContextFilter(logging.Filter):
    """Attach request context information to log records when available."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - exercised indirectly
        if has_request_context():
            record.request_id = getattr(g, "request_id", None)
            record.method = getattr(request, "method", None)
            record.path = getattr(request, "path", None)
            record.remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
            try:
                record.user_id = current_user.get_id() if current_user.is_authenticated else None
            except Exception:
                record.user_id = None
        else:
            record.request_id = None
            record.method = None
            record.path = None
            record.remote_addr = None
            record.user_id = None

        return True


class JsonFormatter(logging.Formatter):
    """Lightweight JSON log formatter to keep dependencies minimal."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting behavior tested via usage
        log_object: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }

        for key, value in record.__dict__.items():
            if key in standard_attrs or key.startswith("_"):
                continue

            if value is not None:
                log_object[key] = value

        if record.exc_info:
            log_object["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_object["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_object, ensure_ascii=False)


def _is_production_environment() -> bool:
    return os.environ.get("APP_ENV") == "production" or os.environ.get("FLASK_ENV") == "production"


def setup_logging(app) -> None:
    """Configure application logging with request context enrichment."""

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    use_json = _is_production_environment()
    formatter: logging.Formatter = JsonFormatter() if use_json else logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(log_level)
    handler.addFilter(RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    app.logger.handlers.clear()
    app.logger.setLevel(log_level)
    app.logger.propagate = True

    for logger_name in ("flask.app", "werkzeug"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(log_level)
        logger.propagate = True

    logging.captureWarnings(True)
