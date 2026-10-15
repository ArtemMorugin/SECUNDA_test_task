import asyncio
import logging
from collections.abc import Callable
from typing import Any

from faststream.message import StreamMessage
from faststream.middlewares import AckPolicy
from faststream.rabbit import RabbitBroker
from faststream.rabbit.annotations import RabbitMessage
from pydantic import ValidationError

from src.application.use_cases.payments import ProcessPayment
from src.config.logging import log_context
from src.domain.exceptions import PaymentNotFound
from src.infrastructures.broker.schemas import PaymentNew
from src.infrastructures.broker.topology import PaymentTopology

logger = logging.getLogger(__name__)


def decode_raw(message: StreamMessage[Any]) -> bytes:
    return message.body


def register_consumer(
    broker: RabbitBroker,
    topology: PaymentTopology,
    process: Callable[[], ProcessPayment],
    *,
    legacy: bool = False,
    timeout: float = 5,
) -> None:
    async def handoff(
        raw: bytes,
        message: RabbitMessage,
        destination: str,
        attempt: int,
        error: str,
    ) -> None:
        headers = dict(message.headers)
        headers["x-processing-attempt"] = attempt
        headers["x-error-type"] = error
        try:
            async with asyncio.timeout(timeout):
                await broker.publish(
                    raw,
                    exchange=topology.exchange,
                    routing_key=destination,
                    message_id=message.message_id,
                    headers=headers,
                    persist=True,
                    mandatory=True,
                    timeout=timeout,
                    content_type="application/json",
                )
            await message.ack()
            logger.log(
                logging.ERROR
                if destination == topology.dead.routing()
                else logging.WARNING,
                "message_handoff destination=%s attempt=%s reason=%s",
                destination,
                attempt,
                error,
            )
        except Exception:
            logger.exception("message_handoff_failed")
            # Keep the original delivery when the handoff cannot be
            # confirmed.
            # Slow transport retries; the new quorum queue also has a
            # delivery limit.
            await asyncio.sleep(1)
            await message.nack(requeue=True)

    async def handle(raw: bytes, message: RabbitMessage) -> None:
        with log_context(event_id=message.message_id):
            await handle_message(raw, message)

    async def handle_message(raw: bytes, message: RabbitMessage) -> None:
        try:
            event = PaymentNew.model_validate_json(raw)
            attempt = message.headers.get("x-processing-attempt", 1)
            if type(attempt) is not int or not 1 <= attempt <= 3:
                raise ValueError("Invalid attempt counter")
        except ValidationError, ValueError:
            await handoff(
                raw, message, topology.dead.routing(), 3, "InvalidMessage"
            )
            return
        with log_context(event_id=event.event_id, payment_id=event.payment_id):
            logger.debug("payment_message_received attempt=%s", attempt)
            try:
                await process()(event.payment_id)
            except PaymentNotFound:
                await handoff(
                    raw,
                    message,
                    topology.dead.routing(),
                    attempt,
                    "PaymentNotFound",
                )
            except Exception as exc:
                error = type(exc).__name__
                logger.warning(
                    "payment_message_failed attempt=%s error_type=%s",
                    attempt,
                    error,
                    exc_info=True,
                )
                if attempt < 3:
                    await handoff(
                        raw,
                        message,
                        topology.retries[attempt - 1].routing(),
                        attempt + 1,
                        error,
                    )
                else:
                    await handoff(
                        raw,
                        message,
                        topology.dead.routing(),
                        attempt,
                        error,
                    )
            else:
                await message.ack()
                logger.debug("payment_message_acknowledged")

    broker.subscriber(
        topology.main,
        exchange=topology.exchange,
        ack_policy=AckPolicy.MANUAL,
        decoder=decode_raw,
    )(handle)
    if legacy:
        # A separate wrapper prevents one registration from mutating the
        # other.
        async def handle_legacy(raw: bytes, message: RabbitMessage) -> None:
            # Move legacy deliveries into the bounded retry topology
            # before processing.
            attempt = message.headers.get("x-processing-attempt", 1)
            if type(attempt) is not int or not 1 <= attempt <= 3:
                attempt = 3
            await handoff(
                raw,
                message,
                topology.main.routing(),
                attempt,
                "LegacyForwarded",
            )

        broker.subscriber(
            topology.legacy,
            exchange=topology.exchange,
            ack_policy=AckPolicy.MANUAL,
            decoder=decode_raw,
        )(handle_legacy)
