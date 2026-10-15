import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config.database import engine
from src.config.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    logger.info("api_started")
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("api_stopped")
