import httpx
from faststream.rabbit import RabbitBroker

from src.application.use_cases.outbox import PublishOutbox
from src.application.use_cases.webhooks import DeliverWebhook
from src.config.ioc.payments import get_transactions
from src.config.settings import get_settings
from src.config.workers import run_worker
from src.infrastructures.broker.publishers.outbox import RabbitMQEventPublisher
from src.infrastructures.broker.topology import PaymentTopology
from src.infrastructures.webhooks.client import HttpxWebhookClient


async def run_webhook_worker() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=settings.webhook_timeout_seconds, follow_redirects=False
    ) as client:
        work = DeliverWebhook(
            get_transactions(),
            HttpxWebhookClient(client, settings.webhook_timeout_seconds),
            lease_seconds=settings.delivery_lease_seconds,
            max_attempts=settings.webhook_max_attempts,
            retry_base_seconds=settings.retry_base_seconds,
        )
        await run_worker(work, "webhooks", settings.worker_poll_seconds)


async def run_outbox_publisher(
    broker: RabbitBroker, topology: PaymentTopology
) -> None:
    settings = get_settings()
    work = PublishOutbox(
        get_transactions(),
        RabbitMQEventPublisher(
            broker, topology, settings.broker_timeout_seconds
        ),
        lease_seconds=settings.delivery_lease_seconds,
        retry_base_seconds=settings.retry_base_seconds,
    )
    await run_worker(work, "outbox", settings.worker_poll_seconds)
