import asyncio
import hashlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.application.dtos.payments import CreatePaymentData, NewWebhook
from src.application.interfaces.payments import TransactionFactory
from src.domain.entities.payment import Payment
from src.domain.exceptions import (
    DuplicatePaymentKey,
    IdempotencyConflict,
    PaymentNotFound,
)
from src.domain.value_objects.payment import PaymentStatus

logger = logging.getLogger(__name__)


async def simulate_payment(
    min_delay: float, max_delay: float
) -> PaymentStatus:
    await asyncio.sleep(random.uniform(min_delay, max_delay))
    return (
        PaymentStatus.SUCCEEDED
        if random.random() < 0.9
        else PaymentStatus.FAILED
    )


def calculate_request_hash(data: CreatePaymentData) -> str:
    payload = {
        "amount": format(data.amount, ".2f"),
        "currency": data.currency.value,
        "description": data.description,
        "metadata": data.metadata,
        "webhook_url": data.webhook_url,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_replay(payment: Payment, data: CreatePaymentData) -> Payment:
    # Compare stored parameters as well, so pre-normalization hashes
    # remain replayable.
    stored = CreatePaymentData(
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        metadata=payment.metadata,
        webhook_url=payment.webhook_url,
    )
    if calculate_request_hash(stored) != calculate_request_hash(data):
        logger.info(
            "payment_idempotency_conflict",
            extra={"payment_id": str(payment.id)},
        )
        raise IdempotencyConflict(
            "Idempotency-Key was already used with a different request"
        )
    return payment


class CreatePayment:
    def __init__(self, transactions: TransactionFactory) -> None:
        self.transactions = transactions

    async def __call__(self, data: CreatePaymentData, key: str) -> Payment:
        created = False
        try:
            async with self.transactions() as tx:
                existing = await tx.payments.find_by_key(key)
                if existing is not None:
                    payment = check_replay(existing, data)
                else:
                    payment = await tx.payments.add(
                        uuid4(), data, key, calculate_request_hash(data)
                    )
                    event_id = uuid4()
                    await tx.outbox.add(
                        event_id,
                        payment.id,
                        {
                            "event_id": str(event_id),
                            "event_type": "payments.new",
                            "schema_version": 1,
                            "payment_id": str(payment.id),
                            "amount": format(payment.amount, ".2f"),
                            "currency": payment.currency.value,
                        },
                    )
                    created = True
        except DuplicatePaymentKey:
            async with self.transactions() as tx:
                existing = await tx.payments.find_by_key(key)
                if existing is None:
                    raise RuntimeError(
                        "Conflicting payment disappeared"
                    ) from None
                payment = check_replay(existing, data)
        if created:
            logger.info(
                "payment_created status=pending",
                extra={
                    "payment_id": str(payment.id),
                    "event_id": str(event_id),
                },
            )
        else:
            logger.debug(
                "payment_idempotency_replay",
                extra={"payment_id": str(payment.id)},
            )
        return payment


class GetPayment:
    def __init__(self, transactions: TransactionFactory) -> None:
        self.transactions = transactions

    async def __call__(self, payment_id: UUID) -> Payment:
        async with self.transactions() as tx:
            payment = await tx.payments.get(payment_id)
            if payment is None:
                raise PaymentNotFound
            return payment


class ProcessPayment:
    def __init__(
        self,
        transactions: TransactionFactory,
        simulate: Callable[[], Awaitable[PaymentStatus]],
    ) -> None:
        self.transactions = transactions
        self.simulate = simulate

    async def __call__(self, payment_id: UUID) -> None:
        async with self.transactions() as tx:
            payment = await tx.payments.get(payment_id)
            if payment is None:
                raise PaymentNotFound
            if payment.status != PaymentStatus.PENDING:
                logger.debug(
                    "payment_processing_skipped status=%s",
                    payment.status.value,
                    extra={"payment_id": str(payment.id)},
                )
                return

        result = await self.simulate()

        async with self.transactions() as tx:
            payment = await tx.payments.get(payment_id, for_update=True)
            if payment is None:
                raise PaymentNotFound
            if payment.status != PaymentStatus.PENDING:
                logger.debug(
                    "payment_finalization_skipped status=%s",
                    payment.status.value,
                    extra={"payment_id": str(payment.id)},
                )
                return
            now = datetime.now(UTC)
            completed = payment.complete(result, now)
            await tx.payments.save_result(completed)
            delivery_id = uuid4()
            await tx.webhooks.add(
                NewWebhook(
                    id=delivery_id,
                    payment_id=payment.id,
                    url=payment.webhook_url,
                    next_attempt_at=now,
                    payload={
                        "event": "payment.processed",
                        "delivery_id": str(delivery_id),
                        "payment_id": str(payment.id),
                        "status": completed.status.value,
                        "amount": format(payment.amount, ".2f"),
                        "currency": payment.currency.value,
                        "processed_at": now.isoformat(),
                    },
                )
            )
        logger.info(
            "payment_processed status=%s",
            completed.status.value,
            extra={
                "payment_id": str(payment.id),
                "delivery_id": str(delivery_id),
            },
        )
