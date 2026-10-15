from dataclasses import dataclass

from faststream.rabbit import (
    ExchangeType,
    QueueType,
    RabbitBroker,
    RabbitExchange,
    RabbitQueue,
)
from faststream.rabbit.schemas.queue import QuorumQueueArgs


@dataclass(frozen=True)
class PaymentTopology:
    exchange: RabbitExchange
    main: RabbitQueue
    dead: RabbitQueue
    retries: tuple[RabbitQueue, RabbitQueue]
    legacy: RabbitQueue


def build_topology(
    prefix: str, retry_base_seconds: float = 1
) -> PaymentTopology:
    exchange = RabbitExchange(prefix, type=ExchangeType.DIRECT, durable=True)
    dead = RabbitQueue(
        f"{prefix}.dlq", queue_type=QueueType.QUORUM, durable=True
    )
    main_args: QuorumQueueArgs = {
        "x-dead-letter-exchange": prefix,
        "x-dead-letter-routing-key": dead.name,
        "x-dead-letter-strategy": "at-least-once",
        "x-overflow": "reject-publish",
        "x-delivery-limit": 3,
    }
    main = RabbitQueue(
        f"{prefix}.new",
        queue_type=QueueType.QUORUM,
        durable=True,
        routing_key="payments.new",
        arguments=main_args,
    )
    retries = []
    for attempt in (1, 2):
        args: QuorumQueueArgs = {
            "x-message-ttl": max(
                1, int(retry_base_seconds * 2 ** (attempt - 1) * 1000)
            ),
            "x-dead-letter-exchange": prefix,
            "x-dead-letter-routing-key": "payments.new",
            "x-dead-letter-strategy": "at-least-once",
            "x-overflow": "reject-publish",
        }
        retries.append(
            RabbitQueue(
                f"{prefix}.retry.{attempt}",
                queue_type=QueueType.QUORUM,
                durable=True,
                arguments=args,
            )
        )
    legacy = RabbitQueue(
        f"{prefix}.process", durable=True, routing_key="payment.created"
    )
    return PaymentTopology(
        exchange, main, dead, (retries[0], retries[1]), legacy
    )


async def declare_topology(
    broker: RabbitBroker, topology: PaymentTopology, *, legacy: bool = False
) -> None:
    exchange = await broker.declare_exchange(topology.exchange)
    queues = [topology.main, topology.dead, *topology.retries]
    if legacy:
        queues.append(topology.legacy)
    for definition in queues:
        queue = await broker.declare_queue(definition)
        await queue.bind(exchange, routing_key=definition.routing())
