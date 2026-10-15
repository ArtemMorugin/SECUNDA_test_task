from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from src.domain.value_objects.payment import PaymentCurrency, PaymentStatus


class CreatePaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: PaymentCurrency
    description: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: HttpUrl

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Description must not be empty")
        return value


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    payment_id: UUID = Field(validation_alias="id")
    amount: Decimal
    currency: PaymentCurrency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None


class PaymentCreatedResponse(BaseModel):
    payment_id: UUID
    status: PaymentStatus
    created_at: datetime
