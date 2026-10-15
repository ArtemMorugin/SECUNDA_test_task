import pytest
from sqlalchemy import text

from src.application.use_cases.outbox import PublishOutbox
from src.application.use_cases.payments import CreatePayment
from src.infrastructures.broker.publishers.outbox import RabbitMQEventPublisher
from src.infrastructures.db.models.outbox import OutboxEvent
from tests.helpers import payment_data, queue_message, row

pytestmark = pytest.mark.integration


async def make_ready(db):
    async with db.engine.begin() as connection:
        await connection.execute(
            text("UPDATE outbox SET next_attempt_at=now(), locked_until=NULL")
        )


async def test_broker_disconnect_recovery(db, mq):
    broker, topology = mq
    await CreatePayment(db.transactions)(payment_data(), "outbox")
    work = PublishOutbox(
        db.transactions,
        RabbitMQEventPublisher(broker, topology),
        retry_base_seconds=0.01,
    )
    await broker.stop()
    assert await work()
    failed = await row(db, OutboxEvent)
    assert failed.published_at is None and failed.attempts == 1
    await broker.connect()
    from src.infrastructures.broker.topology import declare_topology

    await declare_topology(broker, topology)
    await make_ready(db)
    assert await work()
    message = await queue_message(broker, topology.main)
    assert message.message_id == str(failed.id)
    await message.ack()
    assert (await row(db, OutboxEvent)).published_at is not None


async def test_unroutable_publication_is_not_success(db, mq):
    broker, topology = mq
    await CreatePayment(db.transactions)(payment_data(), "unroutable")
    queue = await broker.declare_queue(topology.main)
    await queue.unbind(topology.exchange.name, routing_key="payments.new")
    work = PublishOutbox(
        db.transactions,
        RabbitMQEventPublisher(broker, topology),
        retry_base_seconds=0.01,
    )
    assert await work()
    event = await row(db, OutboxEvent)
    assert event.published_at is None
    assert event.last_error == "PublishError"
    await queue.bind(topology.exchange.name, routing_key="payments.new")
    await make_ready(db)
    assert await work()
    message = await queue_message(broker, topology.main)
    await message.ack()
    assert (await row(db, OutboxEvent)).published_at is not None


async def test_outbox_exhaustion_preserves_event(db, mq):
    broker, topology = mq
    await CreatePayment(db.transactions)(payment_data(), "exhausted")
    await broker.stop()
    work = PublishOutbox(
        db.transactions,
        RabbitMQEventPublisher(broker, topology),
        retry_base_seconds=0.01,
    )
    for _ in range(3):
        await make_ready(db)
        assert await work()
    assert await work() is False
    event = await row(db, OutboxEvent)
    assert (
        event.attempts == 3
        and event.failed_at is not None
        and event.published_at is None
    )
    await broker.connect()
    from src.infrastructures.broker.topology import declare_topology

    await declare_topology(broker, topology)


async def test_crash_after_publish_repeats_same_event_id(db, mq):
    broker, topology = mq
    await CreatePayment(db.transactions)(payment_data(), "crash")
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    async with db.transactions() as tx:
        job = await tx.outbox.claim(now, now + timedelta(seconds=30), 3)
    publisher = RabbitMQEventPublisher(broker, topology)
    await publisher.publish(job.id, job.payload)
    first = await queue_message(broker, topology.main)
    await first.ack()
    # The publisher died before mark_published; allow its lease to
    # expire.
    async with db.engine.begin() as connection:
        await connection.execute(
            text("UPDATE outbox SET locked_until=now()-interval '1 second'")
        )
    await PublishOutbox(db.transactions, publisher)()
    second = await queue_message(broker, topology.main)
    assert second.message_id == first.message_id == str(job.id)
    await second.ack()
