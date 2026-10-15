import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.application.use_cases.payments import CreatePayment, ProcessPayment
from src.domain.value_objects.payment import PaymentStatus
from src.infrastructures.broker.consumers.payments import register_consumer
from src.infrastructures.db.models.payment import Payment
from src.infrastructures.db.models.webhook_delivery import WebhookDelivery
from tests.helpers import count, eventually, payment_data, queue_message, row

pytestmark = pytest.mark.integration


async def simulate():
    return PaymentStatus.SUCCEEDED


async def publish(broker, topology, payment_id):
    event_id = uuid4()
    payload = {
        "event_id": str(event_id),
        "payment_id": str(payment_id),
        "event_type": "payments.new",
        "schema_version": 1,
    }
    await broker.publish(
        payload,
        exchange=topology.exchange,
        routing_key="payments.new",
        persist=True,
        mandatory=True,
        message_id=str(event_id),
    )
    return payload


async def test_real_consumer_handles_duplicate_delivery(db, mq):
    broker, topology = mq
    payment = await CreatePayment(db.transactions)(payment_data(), "consumer")
    register_consumer(
        broker, topology, lambda: ProcessPayment(db.transactions, simulate)
    )
    await broker.start()
    payload = await publish(broker, topology, payment.id)
    await broker.publish(
        payload,
        exchange=topology.exchange,
        routing_key="payments.new",
        persist=True,
        message_id=payload["event_id"],
    )

    async def completed():
        return await count(db, WebhookDelivery) == 1

    await eventually(completed)
    assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED
    # Give the duplicate time to finish, then check that no second
    # webhook appeared.
    import asyncio

    await asyncio.sleep(0.1)
    assert await count(db, WebhookDelivery) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"payment_id":"invalid"}',
        (
            b'{"event_id":"00000000-0000-0000-0000-000000000000",'
            b'"payment_id":"00000000-0000-0000-0000-000000000001",'
            b'"schema_version":99}'
        ),
    ],
)
async def test_invalid_message_reaches_real_dlq(db, mq, raw):
    broker, topology = mq
    register_consumer(
        broker, topology, lambda: ProcessPayment(db.transactions, simulate)
    )
    await broker.start()
    await broker.publish(
        raw,
        exchange=topology.exchange,
        routing_key="payments.new",
        persist=True,
        content_type="application/json",
    )
    message = await queue_message(broker, topology.dead)
    assert message.body == raw
    assert message.headers["x-error-type"] == "InvalidMessage"
    await message.ack()


async def test_missing_payment_reaches_dlq(db, mq):
    broker, topology = mq
    register_consumer(
        broker, topology, lambda: ProcessPayment(db.transactions, simulate)
    )
    await broker.start()
    await publish(broker, topology, uuid4())
    message = await queue_message(broker, topology.dead)
    assert message.headers["x-error-type"] == "PaymentNotFound"
    await message.ack()


async def test_database_save_failure_retries_three_times_then_dlq(db, mq):
    broker, topology = mq
    payment = await CreatePayment(db.transactions)(
        payment_data(), "db-failure"
    )
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
                "test_consumer_failure CHECK(false) NOT VALID"
            )
        )
    try:
        register_consumer(
            broker, topology, lambda: ProcessPayment(db.transactions, simulate)
        )
        await broker.start()
        await publish(broker, topology, payment.id)
        message = await queue_message(broker, topology.dead)
        assert message.headers["x-processing-attempt"] == 3
        assert message.headers["x-error-type"] == "IntegrityError"
        assert json.loads(message.body)["payment_id"] == str(payment.id)
        await message.ack()
        assert (await row(db, Payment)).status == PaymentStatus.PENDING
        assert await count(db, WebhookDelivery) == 0
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE webhook_deliveries DROP CONSTRAINT "
                    "test_consumer_failure"
                )
            )


async def test_transient_processing_error_recovers_on_third_attempt(db, mq):
    broker, topology = mq
    payment = await CreatePayment(db.transactions)(payment_data(), "transient")
    calls = 0

    async def transient_simulator():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("Transient processing failure")
        return PaymentStatus.SUCCEEDED

    register_consumer(
        broker,
        topology,
        lambda: ProcessPayment(db.transactions, transient_simulator),
    )
    await broker.start()
    await publish(broker, topology, payment.id)

    async def completed():
        return await count(db, WebhookDelivery) == 1

    await eventually(completed)
    assert calls == 3
    assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED


async def test_legacy_event_is_forwarded_without_reprocessing(db, mq):
    broker, topology = mq
    from src.infrastructures.broker.topology import declare_topology

    await declare_topology(broker, topology, legacy=True)
    payment = await CreatePayment(db.transactions)(
        payment_data(), "legacy-event"
    )
    register_consumer(
        broker,
        topology,
        lambda: ProcessPayment(db.transactions, simulate),
        legacy=True,
    )
    await broker.start()
    await broker.publish(
        {"event_id": str(uuid4()), "payment_id": str(payment.id)},
        exchange=topology.exchange,
        routing_key="payment.created",
        persist=True,
    )

    async def completed():
        return await count(db, WebhookDelivery) == 1

    await eventually(completed)
    assert (await row(db, Payment)).status == PaymentStatus.SUCCEEDED
    legacy_queue = await broker.declare_queue(topology.legacy)
    await legacy_queue.delete(if_unused=False, if_empty=False)
