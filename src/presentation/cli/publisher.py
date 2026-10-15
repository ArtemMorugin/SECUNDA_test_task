import asyncio
import logging

from src.config.logging import configure_logging
from src.config.settings import get_settings
from src.config.workers import serve

logger = logging.getLogger(__name__)


async def run_publishing() -> None:
    from src.config.broker import broker, topology
    from src.config.ioc.workers import run_outbox_publisher
    from src.infrastructures.broker.topology import declare_topology

    while True:
        try:
            await broker.connect()
            await declare_topology(broker, topology)
            logger.info("publisher_connected")
            await run_outbox_publisher(broker, topology)
        except Exception:
            logger.exception("publisher_connection_failed")
            await broker.stop()
            await asyncio.sleep(get_settings().worker_poll_seconds)


async def main() -> None:
    configure_logging("publisher")
    logger.info("publisher_starting")
    # Configure safe logging before initializing connection settings.
    from src.config.broker import broker
    from src.config.database import engine
    from src.config.ioc.workers import run_webhook_worker

    # Libraries imported above may install their own handlers.
    configure_logging("publisher")

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(run_publishing())
            group.create_task(run_webhook_worker())
    finally:
        await broker.stop()
        await engine.dispose()
        logger.info("publisher_stopped")


if __name__ == "__main__":
    asyncio.run(serve(main))
