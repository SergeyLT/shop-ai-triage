"""Безопасные структурированные события приложения."""

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from shop_triage.config import Settings

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_ALLOWED_FIELDS = {
    "route",
    "status",
    "duration_ms",
    "category",
    "confidence",
    "escalate",
    "error_category",
    "error_code",
    "error_type",
    "component",
    "operation",
    "stage",
    "provider",
    "model",
    "retryable",
    "total_tokens",
}


def set_request_id(value: str) -> Token[str]:
    """Связать события текущего запроса с серверным идентификатором."""
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    """Удалить идентификатор из контекста после запроса."""
    _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    """Выводить только разрешённые поля без содержимого обращения."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self._environment = (
            settings.environment if settings is not None else os.getenv("APP_ENVIRONMENT", "local")
        )
        self._version = (
            settings.version if settings is not None else os.getenv("APP_VERSION", "dev")
        )

    def format(self, record: logging.LogRecord) -> str:
        """Сериализовать одну запись как одну JSON-строку."""
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": "shop-triage",
            "environment": self._environment,
            "version": self._version,
            "logger": record.name,
            "event": getattr(record, "event", "application_log"),
            "message": record.getMessage(),
            "request_id": _request_id.get(),
        }
        for name in _ALLOWED_FIELDS:
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def configure_logging(settings: Settings) -> logging.Logger:
    """Настроить один обработчик приложения без дублирования записей."""
    logger = logging.getLogger("shop_triage")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings))
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger, level: int, event: str, message: str, **fields: object
) -> None:
    """Записать событие только с явно разрешёнными диагностическими полями."""
    safe_fields = {key: value for key, value in fields.items() if key in _ALLOWED_FIELDS}
    logger.log(level, message, extra={"event": event, **safe_fields})
