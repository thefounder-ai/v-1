from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    app_logger = logging.getLogger("skillorbit")
    if not app_logger.handlers:
        app_handler = logging.StreamHandler()
        app_handler.setFormatter(JsonFormatter())
        app_logger.addHandler(app_handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    # SDK clients can include full URLs and query strings in access logs.
    # Keep our structured graph events while avoiding noisy provider URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid4().hex[:16]


def event_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"access_token", "api_key", "authorization", "password", "token"}
    }
    logger.log(level, message, extra={"structured": safe_fields})


def elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)