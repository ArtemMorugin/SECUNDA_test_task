import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.application.use_cases.payments import CreatePayment, ProcessPayment
from src.application.use_cases.webhooks import DeliverWebhook
from src.domain.value_objects.payment import PaymentStatus
from src.infrastructures.db.models.payment import Payment
from src.infrastructures.db.models.webhook_delivery import WebhookDelivery
from src.infrastructures.webhooks.client import HttpxWebhookClient
from tests.helpers import count, payment_data, row, webhook_receiver

pytestmark = pytest.mark.integration


async def successful():
    return PaymentStatus.SUCCEEDED


async def failed():
    return PaymentStatus.FAILED


async def ready(db):
    async with db.engine.begin() as connection:
        await connection.execute(
            text("UPDATE webhook_deliveries SET next_attempt_at=now()")
        )


@pytest.mark.parametrize(
    "result,simulate", [("succeeded", successful), ("failed", failed)]
)
async def test_processing_and_webhook(db, result, simulate):
    async with webhook_receiver() as receiver:
        payment = await CreatePayment(db.transactions)(
            payment_data(receiver.url), "process"
        )
        process = ProcessPayment(db.transactions, simulate)
        await process(payment.id)
        await process(payment.id)
        assert await count(db, WebhookDelivery) == 1
        stored = await row(db, Payment)
        assert (
            stored.status.value == result and stored.processed_at is not None
        )
        async with httpx.AsyncClient(timeout=1) as client:
            assert await DeliverWebhook(
                db.transactions, HttpxWebhookClient(client)
            )()
        assert (await row(db, WebhookDelivery)).status == "delivered"
        assert receiver.requests[0][1]["status"] == result
        assert receiver.requests[0][1]["payment_id"] == str(payment.id)
        assert (
            receiver.requests[0][0]["idempotency-key"]
            == receiver.requests[0][1]["delivery_id"]
        )


async def test_concurrent_finalization_creates_one_webhook(db):
    payment = await CreatePayment(db.transactions)(
        payment_data(), "concurrent-processing"
    )

    async def simulate():
        await asyncio.sleep(0.05)
        return PaymentStatus.SUCCEEDED

    process = ProcessPayment(db.transactions, simulate)
    await asyncio.gather(*(process(payment.id) for _ in range(8)))
    assert await count(db, WebhookDelivery) == 1
    assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED


async def test_webhook_insert_failure_rolls_back_result(db):
    payment = await CreatePayment(db.transactions)(
        payment_data(), "rollback-status"
    )
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
                "test_webhook_failure CHECK(false) NOT VALID"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            await ProcessPayment(db.transactions, successful)(payment.id)
        assert (await row(db, Payment)).status == PaymentStatus.PENDING
        assert await count(db, WebhookDelivery) == 0
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE webhook_deliveries DROP CONSTRAINT "
                    "test_webhook_failure"
                )
            )


async def test_webhook_retries_then_succeeds(db):
    async with webhook_receiver((503, 503, 200)) as receiver:
        payment = await CreatePayment(db.transactions)(
            payment_data(receiver.url), "retry"
        )
        await ProcessPayment(db.transactions, successful)(payment.id)
        async with httpx.AsyncClient(timeout=1) as client:
            work = DeliverWebhook(
                db.transactions,
                HttpxWebhookClient(client),
                retry_base_seconds=0.01,
            )
            for attempt in range(1, 4):
                await ready(db)
                assert await work()
                delivery = await row(db, WebhookDelivery)
                assert delivery.attempts == attempt
                if attempt < 3:
                    assert delivery.status == "pending"
            assert await work() is False
        assert len(receiver.requests) == 3
        assert len({r[1]["delivery_id"] for r in receiver.requests}) == 1
        assert (await row(db, WebhookDelivery)).status == "delivered"
        assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED


@pytest.mark.parametrize("failure", ["http", "timeout", "network"])
async def test_webhook_exhaustion_does_not_reprocess_payment(db, failure):
    async with webhook_receiver(
        (503,), delay=0.1 if failure == "timeout" else 0
    ) as receiver:
        url = (
            "http://127.0.0.1:1/webhook"
            if failure == "network"
            else receiver.url
        )
        payment = await CreatePayment(db.transactions)(
            payment_data(url), "webhook-fail"
        )
        await ProcessPayment(db.transactions, successful)(payment.id)
        async with httpx.AsyncClient(
            timeout=0.02 if failure == "timeout" else 1
        ) as client:
            work = DeliverWebhook(
                db.transactions,
                HttpxWebhookClient(client),
                retry_base_seconds=0.01,
            )
            for _ in range(3):
                await ready(db)
                await work()
            assert await work() is False
        delivery = await row(db, WebhookDelivery)
        assert delivery.status == "failed" and delivery.attempts == 3
        await ProcessPayment(db.transactions, failed)(payment.id)
        assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED
        assert await count(db, WebhookDelivery) == 1


async def test_webhook_worker_recovers_expired_lease_and_fences_old_owner(db):
    async with webhook_receiver() as receiver:
        payment = await CreatePayment(db.transactions)(
            payment_data(receiver.url), "lease"
        )
        await ProcessPayment(db.transactions, successful)(payment.id)
        now = datetime.now(UTC)
        async with db.transactions() as tx:
            old_job = await tx.webhooks.claim(
                now, now + timedelta(seconds=30), 3
            )
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE webhook_deliveries "
                    "SET locked_until=now()-interval '1 second'"
                )
            )
        async with httpx.AsyncClient(timeout=1) as client:
            await DeliverWebhook(db.transactions, HttpxWebhookClient(client))()
        async with db.transactions() as tx:
            assert not await tx.webhooks.mark_failed(
                old_job, now, "StaleWorker", exhausted=True
            )
        delivery = await row(db, WebhookDelivery)
        assert delivery.status == "delivered" and delivery.attempts == 2
