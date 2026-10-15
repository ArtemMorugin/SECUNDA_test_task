from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.domain.value_objects.payment import PaymentCurrency


class PaymentNew(BaseModel):
    event_id: UUID
    payment_id: UUID
    event_type: Literal["payments.new", "payment.created"] = "payment.created"
    schema_version: Literal[1] = 1
    amount: Decimal | None = None
    currency: PaymentCurrency | None = None
