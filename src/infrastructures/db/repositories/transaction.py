from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.payments import (
    PaymentTransaction,
    TransactionFactory,
)
from src.infrastructures.db.repositories.outbox import (
    SqlAlchemyOutboxRepository,
)
from src.infrastructures.db.repositories.payment import (
    SqlAlchemyPaymentRepository,
)
from src.infrastructures.db.repositories.webhook_delivery import (
    SqlAlchemyWebhookRepository,
)


@dataclass
class SqlAlchemyPaymentTransaction:
    payments: SqlAlchemyPaymentRepository
    outbox: SqlAlchemyOutboxRepository
    webhooks: SqlAlchemyWebhookRepository


def transaction_factory(
    sessions: async_sessionmaker[AsyncSession],
) -> TransactionFactory:
    @asynccontextmanager
    async def transaction() -> AsyncIterator[PaymentTransaction]:
        async with sessions() as session, session.begin():
            yield SqlAlchemyPaymentTransaction(
                SqlAlchemyPaymentRepository(session),
                SqlAlchemyOutboxRepository(session),
                SqlAlchemyWebhookRepository(session),
            )

    return transaction
