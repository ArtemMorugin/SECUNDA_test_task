from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.domain.value_objects.payment import PaymentCurrency


@dataclass(frozen=True, kw_only=True)
class CreatePaymentData:
    amount: Decimal
    currency: PaymentCurrency
    description: str
    metadata: dict[str, Any]
    webhook_url: str


@dataclass(frozen=True, kw_only=True)
class OutboxMessage:
    id: UUID
    payload: dict[str, Any]
    attempts: int
    token: UUID


@dataclass(frozen=True, kw_only=True)
class WebhookJob:
    id: UUID
    url: str
    payload: dict[str, Any]
    attempts: int
    token: UUID


@dataclass(frozen=True, kw_only=True)
class NewWebhook:
    id: UUID
    payment_id: UUID
    url: str
    payload: dict[str, Any]
    next_attempt_at: datetime
