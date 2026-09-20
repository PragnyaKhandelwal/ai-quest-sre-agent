"""
Structured JSON Logging Configuration
======================================
Production-grade logging that outputs machine-parseable JSON.
Compatible with: Datadog, CloudWatch, Grafana Loki, ELK Stack.

Every log line includes:
- timestamp (ISO8601)
- level
- logger name
- message
- service / version (this deployment)
- plus whatever extra fields the caller passes via `logger.info(msg, extra={...})`
  (e.g. incident_id, agent_name, request_id), since pythonjsonlogger includes
  any extra attributes on the LogRecord automatically.
"""
import logging
import sys

from pythonjsonlogger import jsonlogger

SERVICE_NAME = "sre-agent-backend"
SERVICE_VERSION = "2.0.1"


class SREJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["service"] = SERVICE_NAME
        log_record["version"] = SERVICE_VERSION


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured JSON logging for the entire application. Safe to
    call more than once (e.g. once at import time, once from a test) --
    clears existing handlers first so log lines are never duplicated."""
    formatter = SREJsonFormatter(fmt="%(timestamp)s %(level)s %(logger)s %(message)s")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Suppress noisy third-party loggers so the JSON stream stays signal, not noise.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger; formatting is inherited from the root handler
    configured by setup_logging()."""
    return logging.getLogger(name)
