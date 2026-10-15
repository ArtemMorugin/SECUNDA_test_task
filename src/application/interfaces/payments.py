from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.application.dtos.payments import (
    CreatePaymentData,
    NewWebhook,
    OutboxMessage,
    WebhookJob,
)
from src.domain.entities.payment import Payment


class PaymentRepository(Protocol):
    async def get(
        self, payment_id: UUID, *, for_update: bool = False
    ) -> Payment | None: ...
    async def find_by_key(self, key: str) -> Payment | None: ...
    async def add(
        self,
        payment_id: UUID,
        data: CreatePaymentData,
        key: str,
        request_hash: str,
    ) -> Payment: ...
    async def save_result(self, payment: Payment) -> None: ...


class OutboxRepository(Protocol):
    async def add(
        self, event_id: UUID, payment_id: UUID, payload: dict[str, Any]
    ) -> None: ...
    async def claim(
        self, now: datetime, locked_until: datetime, max_attempts: int
    ) -> OutboxMessage | None: ...
    async def mark_published(
        self, job: OutboxMessage, now: datetime
    ) -> bool: ...
    async def mark_failed(
        self,
        job: OutboxMessage,
        now: datetime,
        next_attempt: datetime,
        error: str,
        *,
        exhausted: bool,
    ) -> bool: ...


class WebhookRepository(Protocol):
    async def add(self, delivery: NewWebhook) -> None: ...
    async def claim(
        self, now: datetime, locked_until: datetime, max_attempts: int
    ) -> WebhookJob | None: ...
    async def mark_delivered(self, job: WebhookJob, now: datetime) -> bool: ...
    async def mark_failed(
        self,
        job: WebhookJob,
        next_attempt: datetime,
        error: str,
        *,
        exhausted: bool,
    ) -> bool: ...


class PaymentTransaction(Protocol):
    @property
    def payments(self) -> PaymentRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def webhooks(self) -> WebhookRepository: ...


type TransactionFactory = Callable[
    [], AbstractAsyncContextManager[PaymentTransaction]
]


class EventPublisher(Protocol):
    async def publish(
        self, event_id: UUID, payload: dict[str, Any]
    ) -> None: ...


class WebhookClient(Protocol):
    async def send(
        self, delivery_id: UUID, url: str, payload: dict[str, Any]
    ) -> None: ...
