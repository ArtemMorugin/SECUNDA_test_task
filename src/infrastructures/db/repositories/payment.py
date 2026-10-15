from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.payments import CreatePaymentData
from src.domain.entities.payment import Payment
from src.domain.exceptions import DuplicatePaymentKey
from src.domain.value_objects.payment import PaymentStatus
from src.infrastructures.db.models.payment import Payment as PaymentModel


def to_payment(row: PaymentModel) -> Payment:
    return Payment(
        id=row.id,
        amount=row.amount,
        currency=row.currency,
        description=row.description,
        metadata=row.metadata_,
        status=row.status,
        idempotency_key=row.idempotency_key,
        webhook_url=row.webhook_url,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


class SqlAlchemyPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, payment_id: UUID, *, for_update: bool = False
    ) -> Payment | None:
        query = select(PaymentModel).where(PaymentModel.id == payment_id)
        if for_update:
            query = query.with_for_update()
        row = await self.session.scalar(query)
        return to_payment(row) if row is not None else None

    async def find_by_key(self, key: str) -> Payment | None:
        row = await self.session.scalar(
            select(PaymentModel).where(PaymentModel.idempotency_key == key)
        )
        return to_payment(row) if row is not None else None

    async def add(
        self,
        payment_id: UUID,
        data: CreatePaymentData,
        key: str,
        request_hash: str,
    ) -> Payment:
        row = PaymentModel(
            id=payment_id,
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            metadata_=data.metadata,
            status=PaymentStatus.PENDING,
            idempotency_key=key,
            request_hash=request_hash,
            webhook_url=data.webhook_url,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            cause = getattr(exc.orig, "__cause__", None)
            if (
                getattr(exc.orig, "sqlstate", None) == "23505"
                and getattr(cause, "constraint_name", None)
                == "uq_payments_idempotency_key"
            ):
                raise DuplicatePaymentKey from exc
            raise
        return to_payment(row)

    async def save_result(self, payment: Payment) -> None:
        row = await self.session.get(PaymentModel, payment.id)
        if row is None:
            raise RuntimeError("Locked payment disappeared")
        row.status = payment.status
        row.processed_at = payment.processed_at
