import logging
from datetime import UTC, datetime, timedelta

from src.application.interfaces.payments import (
    EventPublisher,
    TransactionFactory,
)

logger = logging.getLogger(__name__)


class PublishOutbox:
    def __init__(
        self,
        transactions: TransactionFactory,
        publisher: EventPublisher,
        *,
        lease_seconds: float = 30,
        max_attempts: int = 3,
        retry_base_seconds: float = 1,
    ) -> None:
        self.transactions = transactions
        self.publisher = publisher
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    async def __call__(self) -> bool:
        now = datetime.now(UTC)
        async with self.transactions() as tx:
            job = await tx.outbox.claim(
                now,
                now + timedelta(seconds=self.lease_seconds),
                self.max_attempts,
            )
        if job is None:
            return False
        context = {
            "event_id": str(job.id),
            "payment_id": job.payload.get("payment_id"),
        }
        logger.debug(
            "outbox_attempt_started attempt=%s", job.attempts, extra=context
        )
        try:
            await self.publisher.publish(job.id, job.payload)
        except Exception as exc:
            now = datetime.now(UTC)
            exhausted = job.attempts >= self.max_attempts
            async with self.transactions() as tx:
                updated = await tx.outbox.mark_failed(
                    job,
                    now,
                    now
                    + timedelta(
                        seconds=self.retry_base_seconds
                        * 2 ** (job.attempts - 1)
                    ),
                    type(exc).__name__,
                    exhausted=exhausted,
                )
            if not updated:
                logger.debug(
                    "outbox_result_ignored stale_claim", extra=context
                )
                return True
            logger.log(
                logging.ERROR if exhausted else logging.WARNING,
                "outbox_%s attempt=%s error_type=%s",
                "exhausted" if exhausted else "retry_scheduled",
                job.attempts,
                type(exc).__name__,
                extra=context,
                exc_info=exhausted,
            )
        else:
            async with self.transactions() as tx:
                updated = await tx.outbox.mark_published(
                    job, datetime.now(UTC)
                )
            if updated:
                logger.info("outbox_published", extra=context)
            else:
                logger.debug(
                    "outbox_result_ignored stale_claim", extra=context
                )
        return True
