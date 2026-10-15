import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from src.application.dtos.payments import CreatePaymentData
from src.domain.value_objects.payment import PaymentCurrency


def payment_data(url="http://127.0.0.1:9999/webhook", amount="100.00"):
    return CreatePaymentData(
        amount=Decimal(amount),
        currency=PaymentCurrency.RUB,
        description="Test payment",
        metadata={"order": 123},
        webhook_url=url,
    )


async def row(db, model):
    async with db.sessions() as session:
        return await session.scalar(select(model))


async def count(db, model):
    async with db.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def eventually(predicate, timeout=8):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.02)


async def queue_message(broker, definition, timeout=8):
    queue = await broker.declare_queue(definition)
    async with asyncio.timeout(timeout):
        while True:
            message = await queue.get(fail=False)
            if message is not None:
                return message
            await asyncio.sleep(0.02)


@dataclass
class WebhookReceiver:
    url: str
    requests: list
    port: int


@asynccontextmanager
async def webhook_receiver(statuses=(200,), delay=0, host="127.0.0.1"):
    requests = []
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            headers = {}
            for line in header.decode().split("\r\n")[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            body = await reader.readexactly(
                int(headers.get("content-length", "0"))
            )
            requests.append((headers, json.loads(body)))
            status = statuses[min(len(requests) - 1, len(statuses) - 1)]
            await asyncio.sleep(delay)
            writer.write(
                (
                    f"HTTP/1.1 {status} Response\r\n"
                    "Content-Length: 0\r\nConnection: close\r\n\r\n"
                ).encode()
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, host, 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield WebhookReceiver(
            f"http://127.0.0.1:{port}/webhook", requests, port
        )
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
