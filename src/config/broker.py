import logging

from faststream.rabbit import Channel, RabbitBroker

from src.config.settings import get_settings
from src.infrastructures.broker.topology import build_topology


def create_broker() -> RabbitBroker:
    settings = get_settings()
    return RabbitBroker(
        settings.rabbitmq_url,
        logger=logging.getLogger("src.config.broker"),
        log_level=getattr(logging, settings.log_level),
        timeout=settings.broker_timeout_seconds,
        default_channel=Channel(
            prefetch_count=10, publisher_confirms=True, on_return_raises=True
        ),
    )


broker = create_broker()
settings = get_settings()
topology = build_topology(
    settings.rabbitmq_prefix, settings.retry_base_seconds
)
