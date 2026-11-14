"""Shared JSON logging for command-line services."""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from types import TracebackType


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            event["stack"] = self.formatStack(record.stack_info)
        return json.dumps(event, ensure_ascii=True)


def configure_logging(service: str) -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Invalid LOG_LEVEL: {level!r}")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(os.environ.get("LOG_SERVICE_NAME", service)))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.captureWarnings(True)

    def uncaught_exception(
        exc_type: type[BaseException],
        value: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, value, traceback)
            return
        logging.getLogger(service).critical(
            "Unhandled exception", exc_info=(exc_type, value, traceback)
        )

    sys.excepthook = uncaught_exception
