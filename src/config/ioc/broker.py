from src.config.broker import broker, topology
from src.config.ioc.payments import get_process_payment
from src.config.settings import get_settings
from src.infrastructures.broker.consumers.payments import register_consumer


def configure_consumers() -> None:
    settings = get_settings()
    register_consumer(
        broker,
        topology,
        get_process_payment,
        legacy=settings.consume_legacy_queue,
        timeout=settings.broker_timeout_seconds,
    )
