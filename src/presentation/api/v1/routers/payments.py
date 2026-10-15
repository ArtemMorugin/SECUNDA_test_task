from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from src.application.dtos.payments import CreatePaymentData
from src.application.use_cases.payments import CreatePayment, GetPayment
from src.config.ioc.payments import get_create_payment, get_payment
from src.presentation.api.security import require_api_key
from src.presentation.api.v1.schemas.payment import (
    CreatePaymentRequest,
    PaymentCreatedResponse,
    PaymentResponse,
)

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["Payments"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    response_model=PaymentCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_payment_endpoint(
    data: CreatePaymentRequest,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
    create_payment: Annotated[CreatePayment, Depends(get_create_payment)],
) -> PaymentCreatedResponse:
    payment = await create_payment(
        CreatePaymentData(
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            metadata=data.metadata,
            webhook_url=str(data.webhook_url),
        ),
        idempotency_key,
    )
    return PaymentCreatedResponse(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment_endpoint(
    payment_id: UUID,
    get: Annotated[GetPayment, Depends(get_payment)],
) -> PaymentResponse:
    return PaymentResponse.model_validate(await get(payment_id))
