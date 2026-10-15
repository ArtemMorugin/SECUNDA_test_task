from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.use_cases.payments import (
    ProcessPayment,
    calculate_request_hash,
    check_replay,
)
from src.domain.entities.payment import Payment
from src.domain.exceptions import IdempotencyConflict, InvalidPaymentTransition
from src.domain.value_objects.payment import PaymentStatus
from tests.helpers import payment_data


def pending_payment():
    data = payment_data()
    return Payment(
        id=uuid4(),
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        metadata=data.metadata,
        status=PaymentStatus.PENDING,
        idempotency_key="test",
        webhook_url=data.webhook_url,
        created_at=datetime.now(UTC),
        processed_at=None,
    )


def test_decimal_representation_is_idempotent():
    assert calculate_request_hash(
        payment_data(amount="100")
    ) == calculate_request_hash(payment_data(amount="100.00"))
    assert check_replay(pending_payment(), payment_data(amount="100"))


def test_changed_parameters_conflict():
    with pytest.raises(IdempotencyConflict):
        check_replay(pending_payment(), payment_data(amount="101"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "changed"),
        ("metadata", {"order": 456}),
        ("webhook_url", "http://example.org/new"),
    ],
)
def test_changed_non_amount_parameters_conflict(field, value):
    with pytest.raises(IdempotencyConflict):
        check_replay(
            pending_payment(), replace(payment_data(), **{field: value})
        )


def test_final_status_cannot_be_changed():
    completed = pending_payment().complete(
        PaymentStatus.SUCCEEDED, datetime.now(UTC)
    )
    with pytest.raises(InvalidPaymentTransition):
        completed.complete(PaymentStatus.FAILED, datetime.now(UTC))
    with pytest.raises(InvalidPaymentTransition):
        pending_payment().complete(PaymentStatus.PENDING, datetime.now(UTC))


async def test_completed_payment_skips_simulator():
    payment = pending_payment().complete(
        PaymentStatus.FAILED, datetime.now(UTC)
    )
    repository = SimpleNamespace(get=AsyncMock(return_value=payment))

    @asynccontextmanager
    async def transactions():
        yield SimpleNamespace(payments=repository)

    simulator = AsyncMock()
    await ProcessPayment(transactions, simulator)(payment.id)
    simulator.assert_not_awaited()


async def test_processing_has_no_open_transaction_during_simulation():
    payment = pending_payment()
    active = False
    repository = SimpleNamespace(
        get=AsyncMock(return_value=payment), save_result=AsyncMock()
    )
    webhooks = SimpleNamespace(add=AsyncMock())

    @asynccontextmanager
    async def transactions():
        nonlocal active
        active = True
        try:
            yield SimpleNamespace(payments=repository, webhooks=webhooks)
        finally:
            active = False

    async def simulate():
        assert not active
        return PaymentStatus.SUCCEEDED

    await ProcessPayment(transactions, simulate)(payment.id)
    assert (
        repository.save_result.await_args.args[0].status
        == PaymentStatus.SUCCEEDED
    )
    delivery = webhooks.add.await_args.args[0]
    assert delivery.payload["payment_id"] == str(payment.id)
    assert delivery.payload["status"] == "succeeded"


@pytest.mark.parametrize(
    "random_value,expected",
    [
        (0, PaymentStatus.SUCCEEDED),
        (0.899, PaymentStatus.SUCCEEDED),
        (0.9, PaymentStatus.FAILED),
        (0.999, PaymentStatus.FAILED),
    ],
)
async def test_simulation_probability_boundary(
    monkeypatch, random_value, expected
):
    from src.application.use_cases import payments

    sleeper = AsyncMock()
    monkeypatch.setattr(payments.asyncio, "sleep", sleeper)
    monkeypatch.setattr(payments.random, "uniform", lambda low, high: 3)
    monkeypatch.setattr(payments.random, "random", lambda: random_value)
    assert await payments.simulate_payment(2, 5) == expected
    sleeper.assert_awaited_once_with(3)
