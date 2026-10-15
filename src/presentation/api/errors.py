import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from src.domain.exceptions import IdempotencyConflict, PaymentNotFound

logger = logging.getLogger(__name__)


async def payment_not_found(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"detail": "Payment not found"}
    )


async def idempotency_conflict(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "Idempotency-Key was already used with a different request"
            )
        },
    )


async def database_unavailable(_: Request, __: Exception) -> JSONResponse:
    logger.error(
        "database_unavailable error_type=%s",
        type(__).__name__,
        exc_info=(type(__), __, __.__traceback__),
    )
    return JSONResponse(
        status_code=503, content={"detail": "Database temporarily unavailable"}
    )


async def unexpected_error(request: Request, _: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={"X-Request-ID": request.state.request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PaymentNotFound, payment_not_found)
    app.add_exception_handler(IdempotencyConflict, idempotency_conflict)
    app.add_exception_handler(SQLAlchemyError, database_unavailable)
    app.add_exception_handler(OSError, database_unavailable)
    app.add_exception_handler(Exception, unexpected_error)
