from datetime import datetime
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.payments import NewWebhook, WebhookJob
from src.infrastructures.db.models.webhook_delivery import WebhookDelivery


class SqlAlchemyWebhookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, delivery: NewWebhook) -> None:
        self.session.add(
            WebhookDelivery(
                id=delivery.id,
                payment_id=delivery.payment_id,
                url=delivery.url,
                payload=delivery.payload,
                next_attempt_at=delivery.next_attempt_at,
            )
        )
        await self.session.flush()

    async def claim(
        self, now: datetime, locked_until: datetime, max_attempts: int
    ) -> WebhookJob | None:
        available = or_(
            WebhookDelivery.locked_until.is_(None),
            WebhookDelivery.locked_until <= now,
        )
        await self.session.execute(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.attempts >= max_attempts,
                available,
            )
            .values(
                status="failed",
                locked_until=None,
                claim_token=None,
                last_error="AttemptLeaseExpired",
            )
        )
        row = await self.session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.attempts < max_attempts,
                WebhookDelivery.next_attempt_at <= now,
                available,
            )
            .order_by(WebhookDelivery.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = uuid4()
        row.claim_token = token
        row.locked_until = locked_until
        row.attempts += 1
        return WebhookJob(
            id=row.id,
            url=row.url,
            payload=row.payload,
            attempts=row.attempts,
            token=token,
        )

    async def mark_delivered(self, job: WebhookJob, now: datetime) -> bool:
        updated_id = await self.session.scalar(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.id == job.id,
                WebhookDelivery.claim_token == job.token,
                WebhookDelivery.status == "pending",
            )
            .values(
                status="delivered",
                delivered_at=now,
                locked_until=None,
                claim_token=None,
                last_error=None,
            )
            .returning(WebhookDelivery.id)
        )
        return updated_id is not None

    async def mark_failed(
        self,
        job: WebhookJob,
        next_attempt: datetime,
        error: str,
        *,
        exhausted: bool,
    ) -> bool:
        updated_id = await self.session.scalar(
            update(WebhookDelivery)
            .where(
                WebhookDelivery.id == job.id,
                WebhookDelivery.claim_token == job.token,
                WebhookDelivery.status == "pending",
            )
            .values(
                status="failed" if exhausted else "pending",
                next_attempt_at=next_attempt,
                last_error=error,
                locked_until=None,
                claim_token=None,
            )
            .returning(WebhookDelivery.id)
        )
        return updated_id is not None
