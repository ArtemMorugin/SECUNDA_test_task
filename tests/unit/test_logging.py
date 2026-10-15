import asyncio
import io
import logging
import os
import re
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.config.logging import (
    LOG_CONTEXT,
    ConsoleFormatter,
    ContextFilter,
    log_context,
    safe_identifier,
)
from src.presentation.api.middleware import RequestLoggingMiddleware


@pytest.fixture
def console():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter("test"))
    handler.setFormatter(
        ConsoleFormatter(
            "%(asctime)s %(levelname)s [%(service)s] %(name)s: %(message)s",
            secrets=("private-api-key", "private-password"),
        )
    )
    logger = logging.getLogger(f"src.test.{uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    yield logger, stream
    logger.removeHandler(handler)
    handler.close()


def test_context_reset_after_exception(console):
    logger, stream = console
    request_id = uuid4()
    with pytest.raises(RuntimeError), log_context(request_id=request_id):
        logger.info("inside")
        raise RuntimeError
    logger.info("outside")
    lines = stream.getvalue().splitlines()
    assert f"request_id={request_id}" in lines[0]
    assert "request_id=" not in lines[1]
    assert LOG_CONTEXT.get() is None


async def test_context_isolated_between_concurrent_tasks(console):
    logger, stream = console
    ids = [uuid4(), uuid4()]

    async def work(index):
        with log_context(request_id=ids[index]):
            await asyncio.sleep(0)
            logger.info("task=%s", index)

    await asyncio.gather(work(0), work(1))
    for index, request_id in enumerate(ids):
        line = next(
            line
            for line in stream.getvalue().splitlines()
            if f"task={index}" in line
        )
        assert f"request_id={request_id}" in line
        assert str(ids[1 - index]) not in line
    assert LOG_CONTEXT.get() is None


def test_safe_traceback_omits_exception_text_and_locals(console):
    logger, stream = console
    personal_data = "private-person@example.org"
    try:
        raise ValueError(f"SQL params: {personal_data} private-password")
    except ValueError:
        logger.exception("database_failed")
    output = stream.getvalue()
    assert "Traceback (most recent call last):" in output
    assert "test_logging.py:" in output
    assert "ValueError" in output
    assert personal_data not in output
    assert "SQL params" not in output
    assert "private-password" not in output


def test_urls_secrets_and_control_characters_are_redacted(console):
    logger, stream = console
    logger.warning(
        "failure token=%s endpoint=%s text=%s",
        "private-api-key",
        "https://user:private-password@private.invalid/path?token=secret",
        "first\nFORGED\x1b[31m",
    )
    output = stream.getvalue()
    assert len(output.splitlines()) == 1
    assert "private-api-key" not in output
    assert "private-password" not in output
    assert "private.invalid" not in output
    assert "[redacted]" in output and "[url-redacted]" in output
    assert "\\nFORGED\\x1b" in output


def test_formatter_does_not_mutate_original_message():
    record = logging.LogRecord(
        "src.test",
        logging.INFO,
        __file__,
        1,
        "token=%s",
        ("private-api-key",),
        None,
    )
    formatter = ConsoleFormatter("%(message)s", secrets=("private-api-key",))
    assert formatter.format(record) == "token=[redacted]"
    assert record.msg == "token=%s"
    assert record.args == ("private-api-key",)


def test_timestamp_uses_utc_milliseconds(console):
    logger, stream = console
    record = logging.LogRecord(
        "src.test", logging.INFO, __file__, 1, "event", (), None
    )
    record.created = 0.123
    logger.handle(record)
    assert stream.getvalue().startswith("1970-01-01T00:00:00.123Z")


@pytest.mark.parametrize("value", [None, "malicious\nvalue", "x" * 100, 42])
def test_invalid_context_identifiers_are_not_logged(value):
    assert safe_identifier(value) == "-"


def test_configure_twice_emits_once_to_stdout_and_no_files():
    code = """
import logging
import sys
from src.config.logging import configure_logging
configure_logging("test")
configure_logging("test")
handlers = logging.getLogger().handlers
assert len(handlers) == 1
assert type(handlers[0]) is logging.StreamHandler
assert handlers[0].stream is sys.stdout
logging.getLogger("src.test").info("single_event")
logging.getLogger("uvicorn.error").info("server_event")
logging.getLogger("uvicorn.access").info("raw_path_secret")
logging.getLogger("httpx").info("raw_http_url_secret")
logging.getLogger("sqlalchemy.engine").warning("SQL_parameter_secret")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        env=os.environ | {"LOG_LEVEL": "INFO"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert result.stdout.count("single_event") == 1
    assert "server_event" in result.stdout
    assert "secret" not in result.stdout
    assert re.match(r"\d{4}-\d{2}-\d{2}T.+Z INFO", lines[0])


@pytest.mark.parametrize("provided", [str(uuid4()), "bad-private-api-key"])
async def test_request_ids_and_logs_omit_request_data(
    console, monkeypatch, provided
):
    logger, stream = console
    monkeypatch.setattr("src.presentation.api.middleware.logger", logger)

    async def backend(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/payments/{payment_id}")
        logger.info("application_event")
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async with AsyncClient(
        transport=ASGITransport(app=RequestLoggingMiddleware(backend)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/private-path?token=private-api-key",
            headers={"X-Request-ID": provided, "X-API-Key": "private-api-key"},
            json={"email": "private-person@example.org"},
        )
    request_id = response.headers["X-Request-ID"]
    assert UUID(request_id)
    if provided.startswith("bad"):
        assert request_id != provided
    else:
        assert request_id == provided
    output = stream.getvalue()
    assert output.count(f"request_id={request_id}") == 2
    assert "route=/payments/{payment_id}" in output
    assert "status=200" in output and "duration_ms=" in output
    for sensitive in ("private-path", "private-api-key", "private-person"):
        assert sensitive not in output


async def test_unexpected_api_error_returns_safe_body_and_request_id(
    console, monkeypatch
):
    from src.presentation.api.errors import unexpected_error

    logger, stream = console
    monkeypatch.setattr("src.presentation.api.middleware.logger", logger)
    application = FastAPI()
    application.add_middleware(RequestLoggingMiddleware)
    application.add_exception_handler(Exception, unexpected_error)

    @application.get("/failure")
    async def failure():
        raise RuntimeError("private-person@example.org private-api-key")

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/failure")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert UUID(response.headers["X-Request-ID"])
    output = stream.getvalue()
    assert "http_request_failed" in output and "status=500" in output
    assert "private-person" not in output
    assert "private-api-key" not in output


def test_service_fatal_error_logs_once_and_exits_without_raw_traceback():
    code = """
import asyncio
from src.config.logging import configure_logging
from src.config.workers import serve
configure_logging("test")
async def fail():
    raise RuntimeError("private-sensitive-exception-text")
asyncio.run(serve(fail))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "service_failed" in result.stdout
    assert "RuntimeError" in result.stdout
    assert "private-sensitive" not in result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "module",
    [
        "src.presentation.cli.api",
        "src.presentation.cli.consumer",
        "src.presentation.cli.publisher",
    ],
)
def test_invalid_startup_settings_do_not_expose_credentials(module):
    result = subprocess.run(
        [sys.executable, "-B", "-m", module],
        env=os.environ
        | {
            "API_KEY": "private-bootstrap-api-key",
            "DATABASE_URL": (
                "postgresql+asyncpg://postgres:private-bootstrap-password"
                "@localhost:15432/payments_test"
            ),
            "DELIVERY_LEASE_SECONDS": "1",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "configuration_invalid" in result.stdout
    assert "ValidationError" in result.stdout
    assert "private-bootstrap" not in result.stdout + result.stderr
    assert result.stderr == ""


async def test_payment_success_is_not_logged_before_commit(caplog):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from src.application.use_cases.payments import CreatePayment
    from tests.helpers import payment_data
    from tests.unit.test_payments import pending_payment

    @asynccontextmanager
    async def transactions():
        yield SimpleNamespace(
            payments=SimpleNamespace(
                find_by_key=AsyncMock(return_value=None),
                add=AsyncMock(return_value=pending_payment()),
            ),
            outbox=SimpleNamespace(add=AsyncMock()),
        )
        raise RuntimeError("Commit failed")

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await CreatePayment(transactions)(payment_data(), "test-key")
    assert not any(
        record.getMessage().startswith("payment_created")
        for record in caplog.records
    )


async def test_stale_outbox_owner_does_not_log_false_success(caplog):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from src.application.dtos.payments import OutboxMessage
    from src.application.use_cases.outbox import PublishOutbox

    job = OutboxMessage(
        id=uuid4(),
        payload={"payment_id": str(uuid4())},
        attempts=1,
        token=uuid4(),
    )

    @asynccontextmanager
    async def transactions():
        yield SimpleNamespace(
            outbox=SimpleNamespace(
                claim=AsyncMock(return_value=job),
                mark_published=AsyncMock(return_value=False),
            )
        )

    with caplog.at_level(
        logging.DEBUG, logger="src.application.use_cases.outbox"
    ):
        await PublishOutbox(
            transactions, SimpleNamespace(publish=AsyncMock())
        )()
    messages = [record.getMessage() for record in caplog.records]
    assert "outbox_published" not in messages
    assert "outbox_result_ignored stale_claim" in messages
