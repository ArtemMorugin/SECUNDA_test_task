import asyncio
import logging

from src.config.logging import configure_logging
from src.config.settings import get_settings
from src.config.workers import run_worker, serve

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging("consumer")
    logger.info("consumer_starting")
    # Configure safe logging before initializing connection settings.
    from src.config.broker import broker, topology
    from src.config.database import engine
    from src.config.ioc.broker import configure_consumers
    from src.infrastructures.broker.topology import declare_topology

    # Libraries imported above may install their own handlers.
    configure_logging("consumer")

    settings = get_settings()
    configure_consumers()
    try:
        await broker.connect()
        await declare_topology(
            broker, topology, legacy=settings.consume_legacy_queue
        )
        await broker.start()
        logger.info("consumer_started")

        async def heartbeat() -> bool:
            if not await broker.ping(timeout=settings.broker_timeout_seconds):
                raise RuntimeError("Broker unavailable")
            return False

        await run_worker(heartbeat, "consumer", settings.worker_poll_seconds)
    finally:
        await broker.stop()
        await engine.dispose()
        logger.info("consumer_stopped")


if __name__ == "__main__":
    asyncio.run(serve(main))
