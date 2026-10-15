import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from datetime import UTC, datetime
from logging.config import dictConfig
from pathlib import Path
from traceback import walk_tb
from types import TracebackType
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from src.config.settings import get_settings

CONTEXT_FIELDS = (
    "request_id",
    "event_id",
    "payment_id",
    "delivery_id",
)
LOG_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "log_context", default=None
)
URL_PATTERN = re.compile(
    r"\b(?:https?|amqps?|postgres(?:ql)?(?:\+asyncpg)?)://[^\s]+",
    re.IGNORECASE,
)
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def safe_identifier(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str) and len(value) <= 64:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return "-"


@contextmanager
def log_context(**identifiers: str | UUID | None) -> Iterator[None]:
    context = (LOG_CONTEXT.get() or {}).copy()
    for name, value in identifiers.items():
        if name not in CONTEXT_FIELDS:
            raise ValueError("Unsupported logging context field")
        context[name] = safe_identifier(value)
    token = LOG_CONTEXT.set(context)
    try:
        yield
    finally:
        LOG_CONTEXT.reset(token)


class ContextFilter(logging.Filter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service
        context = LOG_CONTEXT.get() or {}
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, context.get(name))
            if name == "event_id" and value is None:
                value = getattr(record, "message_id", None)
            setattr(record, name, safe_identifier(value))
        return True


class ConsoleFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: tuple[str, ...] = ()) -> None:
        super().__init__(fmt)
        self.secrets = tuple(
            sorted(set(filter(None, secrets)), key=len, reverse=True)
        )

    def redact(self, value: str) -> str:
        value = URL_PATTERN.sub("[url-redacted]", value)
        for secret in self.secrets:
            value = value.replace(secret, "[redacted]")
        return value

    def formatTime(
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        return (
            datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def formatMessage(self, record: logging.LogRecord) -> str:
        rendered = super().formatMessage(record)
        identifiers = []
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, "-")
            if value != "-":
                identifiers.append(f"{name}={self.redact(value)}")
        if identifiers:
            rendered += " " + " ".join(identifiers)
        return rendered

    def formatException(
        self,
        ei: tuple[
            type[BaseException] | None,
            BaseException | None,
            TracebackType | None,
        ],
    ) -> str:
        # Exception text and source lines can contain SQL parameters or
        # URLs.
        lines = ["Traceback (most recent call last):"]
        if ei[2] is not None:
            for frame, line in walk_tb(ei[2]):
                filename = Path(frame.f_code.co_filename).name
                lines.append(f"  {filename}:{line} in {frame.f_code.co_name}")
        lines.append(ei[0].__name__ if ei[0] else "Exception")
        return "\n".join(lines)

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy(record)
        message = self.redact(record.getMessage())
        safe_record.msg = CONTROL_PATTERN.sub(
            lambda match: repr(match.group())[1:-1], message
        )
        safe_record.args = ()
        safe_record.exc_text = None
        safe_record.stack_info = None
        return super().format(safe_record)


def configure_logging(
    service: str, *, stream: Literal["stdout", "stderr"] = "stdout"
) -> None:
    configuration_error: Exception | None = None
    level = "INFO"
    secrets: list[str] = []
    try:
        settings = get_settings()
        level = settings.log_level
        secrets.append(settings.api_key)
        for url in (settings.database_url, settings.rabbitmq_url):
            password = urlsplit(url).password
            if password:
                secrets.extend((password, unquote(password)))
    except Exception as exc:
        configuration_error = exc
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "context": {
                    "()": ContextFilter,
                    "service": service,
                },
            },
            "formatters": {
                "console": {
                    "()": ConsoleFormatter,
                    "fmt": (
                        "%(asctime)s %(levelname)-8s [%(service)s] "
                        "%(name)s: %(message)s"
                    ),
                    "secrets": tuple(secrets),
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": f"ext://sys.{stream}",
                    "formatter": "console",
                    "filters": ["context"],
                },
            },
            "root": {
                "level": level,
                "handlers": ["console"],
            },
            "loggers": {
                name: {
                    "level": level,
                    "handlers": [],
                    "propagate": propagate,
                }
                for name, level, propagate in (
                    ("src", "NOTSET", True),
                    ("uvicorn", "NOTSET", True),
                    ("uvicorn.error", "NOTSET", True),
                    ("uvicorn.access", "CRITICAL", False),
                    ("uvicorn.asgi", "WARNING", True),
                    ("faststream", "WARNING", True),
                    ("httpx", "WARNING", True),
                    ("httpcore", "WARNING", True),
                    ("aio_pika", "WARNING", True),
                    ("aiormq", "WARNING", True),
                    ("sqlalchemy.engine", "CRITICAL", False),
                    ("sqlalchemy.pool", "WARNING", True),
                )
            },
        }
    )
    logging.captureWarnings(True)
    if configuration_error is not None:
        logging.getLogger(__name__).error(
            "configuration_invalid error_type=%s",
            type(configuration_error).__name__,
            exc_info=(
                type(configuration_error),
                configuration_error,
                configuration_error.__traceback__,
            ),
        )
        raise RuntimeError("Invalid service configuration") from None
