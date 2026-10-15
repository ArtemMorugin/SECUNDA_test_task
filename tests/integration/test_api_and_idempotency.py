import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.application.use_cases.payments import CreatePayment, GetPayment
from src.config.ioc.payments import get_create_payment, get_payment
from src.infrastructures.db.models.outbox import OutboxEvent
from src.infrastructures.db.models.payment import Payment
from src.main import app
from tests.helpers import count, payment_data

pytestmark = pytest.mark.integration


async def test_api_create_get_replay_and_conflict(db):
    app.dependency_overrides[get_create_payment] = lambda: CreatePayment(
        db.transactions
    )
    app.dependency_overrides[get_payment] = lambda: GetPayment(db.transactions)
    body = {
        "amount": "100",
        "currency": "RUB",
        "description": "Test payment",
        "metadata": {"order": 123},
        "webhook_url": "https://example.org/webhook",
    }
    headers = {
        "X-API-Key": "integration-test-key",
        "Idempotency-Key": "api-test",
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/payments", json=body, headers=headers
            )
            assert created.status_code == 202
            payment_id = created.json()["payment_id"]
            assert created.json()["status"] == "pending"
            fetched = await client.get(
                f"/api/v1/payments/{payment_id}", headers=headers
            )
            assert fetched.status_code == 200
            assert fetched.json()["metadata"] == {"order": 123}
            assert fetched.json()["idempotency_key"] == "api-test"
            assert fetched.json()["webhook_url"] == body["webhook_url"]
            replay = await client.post(
                "/api/v1/payments",
                json=body | {"amount": "100.00"},
                headers=headers,
            )
            assert replay.json()["payment_id"] == payment_id
            conflict = await client.post(
                "/api/v1/payments",
                json=body | {"amount": "101"},
                headers=headers,
            )
            assert conflict.status_code == 409
            assert (
                await client.get(
                    f"/api/v1/payments/{uuid4()}", headers=headers
                )
            ).status_code == 404
        assert await count(db, Payment) == 1
        assert await count(db, OutboxEvent) == 1
    finally:
        app.dependency_overrides.clear()


async def test_concurrent_identical_keys(db):
    use_case = CreatePayment(db.transactions)
    payments = await asyncio.gather(
        *(
            use_case(
                payment_data(amount="100" if n % 2 else "100.00"), "concurrent"
            )
            for n in range(20)
        )
    )
    assert len({payment.id for payment in payments}) == 1
    assert await count(db, Payment) == 1
    assert await count(db, OutboxEvent) == 1


async def test_legacy_hash_replay(db):
    create = CreatePayment(db.transactions)
    payment = await create(payment_data(), "legacy")
    async with db.engine.begin() as connection:
        await connection.execute(
            text("UPDATE payments SET request_hash = repeat('0',64)")
        )
    assert (
        await create(payment_data(amount="100"), "legacy")
    ).id == payment.id


async def test_outbox_failure_rolls_back_payment(db):
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE outbox ADD CONSTRAINT "
                "test_outbox_failure CHECK (false) NOT VALID"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            await CreatePayment(db.transactions)(payment_data(), "rollback")
        assert await count(db, Payment) == 0
        assert await count(db, OutboxEvent) == 0
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE outbox DROP CONSTRAINT test_outbox_failure")
            )


async def test_database_connection_failure_returns_safe_503(migrated_db):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.infrastructures.db.repositories.transaction import (
        transaction_factory,
    )

    engine = create_async_engine(
        make_url(migrated_db).set(host="127.0.0.1", port=1),
        connect_args={"timeout": 1},
    )
    transactions = transaction_factory(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_create_payment] = lambda: CreatePayment(
        transactions
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/payments",
                headers={
                    "X-API-Key": "integration-test-key",
                    "Idempotency-Key": "db-unavailable",
                },
                json={
                    "amount": "1",
                    "currency": "RUB",
                    "description": "test",
                    "webhook_url": "https://example.org/webhook",
                },
            )
            assert response.status_code == 503
            assert response.json() == {
                "detail": "Database temporarily unavailable"
            }
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
