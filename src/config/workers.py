import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def serve(service: Callable[[], Coroutine[Any, Any, None]]) -> None:
    task = asyncio.create_task(service())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("service_failed")
        raise SystemExit(1) from None
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


async def run_worker(
    work: Callable[[], Awaitable[bool]], name: str, poll_seconds: float
) -> None:
    logger.info("worker_started worker=%s", name)
    while True:
        try:
            busy = await work()
            await asyncio.to_thread(
                Path(f"/tmp/payments-{name}.heartbeat").touch
            )
        except Exception:
            logger.exception("worker_iteration_failed worker=%s", name)
            busy = False
        if not busy:
            await asyncio.sleep(poll_seconds)
