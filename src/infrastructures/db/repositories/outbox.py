from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.payments import OutboxMessage
from src.infrastructures.db.models.outbox import OutboxEvent


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, event_id: UUID, payment_id: UUID, payload: dict[str, Any]
    ) -> None:
        self.session.add(
            OutboxEvent(
                id=event_id,
                aggregate_id=payment_id,
                event_type="payments.new",
                payload=payload,
            )
        )
        await self.session.flush()

    async def claim(
        self, now: datetime, locked_until: datetime, max_attempts: int
    ) -> OutboxMessage | None:
        available = or_(
            OutboxEvent.locked_until.is_(None), OutboxEvent.locked_until <= now
        )
        # A crashed last attempt must eventually become visible as
        # exhausted.
        await self.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.failed_at.is_(None),
                OutboxEvent.attempts >= max_attempts,
                available,
            )
            .values(
                failed_at=now,
                locked_until=None,
                claim_token=None,
                last_error="AttemptLeaseExpired",
            )
        )
        row = await self.session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.failed_at.is_(None),
                OutboxEvent.attempts < max_attempts,
                OutboxEvent.next_attempt_at <= now,
                available,
            )
            .order_by(OutboxEvent.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = uuid4()
        row.claim_token = token
        row.locked_until = locked_until
        row.attempts += 1
        return OutboxMessage(
            id=row.id, payload=row.payload, attempts=row.attempts, token=token
        )

    async def mark_published(self, job: OutboxMessage, now: datetime) -> bool:
        updated_id = await self.session.scalar(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == job.id,
                OutboxEvent.claim_token == job.token,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.failed_at.is_(None),
            )
            .values(
                published_at=now,
                locked_until=None,
                claim_token=None,
                last_error=None,
            )
            .returning(OutboxEvent.id)
        )
        return updated_id is not None

    async def mark_failed(
        self,
        job: OutboxMessage,
        now: datetime,
        next_attempt: datetime,
        error: str,
        *,
        exhausted: bool,
    ) -> bool:
        updated_id = await self.session.scalar(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == job.id,
                OutboxEvent.claim_token == job.token,
                OutboxEvent.published_at.is_(None),
                OutboxEvent.failed_at.is_(None),
            )
            .values(
                locked_until=None,
                claim_token=None,
                last_error=error,
                next_attempt_at=next_attempt,
                failed_at=now if exhausted else None,
            )
            .returning(OutboxEvent.id)
        )
        return updated_id is not None
