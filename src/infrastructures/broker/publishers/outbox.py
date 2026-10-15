import asyncio
import logging
from typing import Any
from uuid import UUID

from faststream.rabbit import RabbitBroker

from src.config.logging import log_context
from src.infrastructures.broker.topology import PaymentTopology

logger = logging.getLogger(__name__)


class RabbitMQEventPublisher:
    def __init__(
        self,
        broker: RabbitBroker,
        topology: PaymentTopology,
        timeout: float = 5,
    ) -> None:
        self.broker = broker
        self.topology = topology
        self.timeout = timeout

    async def publish(self, event_id: UUID, payload: dict[str, Any]) -> None:
        with log_context(
            event_id=event_id,
            payment_id=payload.get("payment_id"),
        ):
            async with asyncio.timeout(self.timeout):
                await self.broker.publish(
                    payload,
                    exchange=self.topology.exchange,
                    routing_key="payments.new",
                    message_id=str(event_id),
                    persist=True,
                    mandatory=True,
                    timeout=self.timeout,
                )
            logger.debug("broker_publish_confirmed")
