from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.domain.exceptions import InvalidPaymentTransition
from src.domain.value_objects.payment import PaymentCurrency, PaymentStatus


@dataclass(frozen=True, kw_only=True)
class Payment:
    id: UUID
    amount: Decimal
    currency: PaymentCurrency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None

    def complete(
        self, status: PaymentStatus, processed_at: datetime
    ) -> Payment:
        if (
            self.status != PaymentStatus.PENDING
            or status == PaymentStatus.PENDING
        ):
            raise InvalidPaymentTransition(
                "Payment can be completed only once"
            )
        return replace(self, status=status, processed_at=processed_at)
