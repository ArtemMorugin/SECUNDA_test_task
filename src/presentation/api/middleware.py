import logging
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config.logging import log_context, safe_identifier

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = "-"
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                request_id = safe_identifier(value.decode("latin-1"))
                break
        if request_id == "-":
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status_code = 500

        async def send_response(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_response)
            except Exception:
                logger.exception("http_request_failed")
                raise
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                level = logging.INFO
                if status_code >= 500:
                    level = logging.ERROR
                elif status_code >= 400:
                    level = logging.WARNING
                elif route == "/health":
                    level = logging.DEBUG
                logger.log(
                    level,
                    "http_request method=%s route=%s status=%s "
                    "duration_ms=%.2f",
                    scope["method"],
                    route,
                    status_code,
                    (perf_counter() - started) * 1000,
                )
