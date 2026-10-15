import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from faststream.rabbit import Channel, RabbitBroker
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

os.environ["API_KEY"] = "integration-test-key"
os.environ["APP_DEBUG"] = "false"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    (
        "postgresql+asyncpg://postgres:integration-test-password"
        "@localhost:15432/payments_test"
    ),
)
os.environ["RABBITMQ_URL"] = os.getenv(
    "TEST_RABBITMQ_URL",
    "amqp://integration:integration-test-password@localhost:15673/",
)

from src.infrastructures.broker.topology import (
    build_topology,
    declare_topology,
)
from src.infrastructures.db.repositories.transaction import transaction_factory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def migrated_db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set TEST_DATABASE_URL for real PostgreSQL integration tests"
        )
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Refusing to modify a database without the _test suffix")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return url


@dataclass
class TestDatabase:
    engine: object
    sessions: async_sessionmaker[AsyncSession]
    transactions: object


@pytest_asyncio.fixture
async def db(migrated_db):
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE webhook_deliveries, outbox, payments CASCADE")
        )
    yield TestDatabase(engine, sessions, transaction_factory(sessions))
    await engine.dispose()


@pytest_asyncio.fixture
async def mq():
    url = os.getenv("TEST_RABBITMQ_URL")
    if not url:
        pytest.skip(
            "Set TEST_RABBITMQ_URL for real RabbitMQ integration tests"
        )
    broker = RabbitBroker(
        url,
        logger=None,
        timeout=3,
        default_channel=Channel(
            prefetch_count=1, publisher_confirms=True, on_return_raises=True
        ),
    )
    topology = build_topology(f"test.{uuid4().hex}", 0.05)
    await broker.connect()
    await declare_topology(broker, topology)
    try:
        yield broker, topology
    finally:
        for definition in (topology.main, topology.dead, *topology.retries):
            queue = await broker.declare_queue(definition)
            await queue.delete(if_unused=False, if_empty=False)
        exchange = await broker.declare_exchange(topology.exchange)
        await exchange.delete(if_unused=False)
        await broker.stop()
