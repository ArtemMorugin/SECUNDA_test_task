"""Verify Compose processing and a real HTTP webhook."""

import asyncio
import json
import os
from uuid import UUID, uuid4

import asyncpg
import httpx
from sqlalchemy.engine import make_url

from tests.helpers import webhook_receiver


async def main() -> None:
    database_url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (database_url.database or "").endswith("_test"):
        raise ValueError(
            "Smoke checks require a database with the _test suffix"
        )
    connection = await asyncpg.connect(
        database_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
    )
    request_id = str(uuid4())
    headers = {
        "X-API-Key": "integration-test-key",
        "Idempotency-Key": str(uuid4()),
        "X-Request-ID": request_id,
    }
    try:
        async with (
            webhook_receiver(host="0.0.0.0") as receiver,
            httpx.AsyncClient(
                base_url="http://127.0.0.1:18000", timeout=5
            ) as client,
        ):
            for path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
                assert (await client.get(path)).status_code == 200
            body = {
                "amount": "42.00",
                "currency": "RUB",
                "description": "Compose smoke payment",
                "metadata": {"test": "compose"},
                "webhook_url": (
                    f"http://host.docker.internal:{receiver.port}/webhook"
                ),
            }
            created = await client.post(
                "/api/v1/payments", headers=headers, json=body
            )
            assert created.status_code == 202, created.text
            assert created.headers["X-Request-ID"] == request_id
            payment_id = created.json()["payment_id"]
            async with asyncio.timeout(30):
                while True:
                    state = await connection.fetchrow(
                        "SELECT p.status::text AS payment_status, "
                        "w.status AS delivery_status, "
                        "o.published_at IS NOT NULL AS published "
                        "FROM payments p JOIN outbox o ON o.aggregate_id=p.id "
                        "LEFT JOIN webhook_deliveries w ON w.payment_id=p.id "
                        "WHERE p.id=$1",
                        UUID(payment_id),
                    )
                    if state and state["delivery_status"] == "delivered":
                        break
                    await asyncio.sleep(0.1)
            assert state["published"]
            assert state["payment_status"] in ("succeeded", "failed")
            assert len(receiver.requests) == 1
            assert receiver.requests[0][1]["payment_id"] == payment_id
            assert receiver.requests[0][1]["status"] == state["payment_status"]
            fetched = await client.get(
                f"/api/v1/payments/{payment_id}", headers=headers
            )
            assert fetched.status_code == 200
            assert fetched.headers["X-Request-ID"] == request_id
            replay = await client.post(
                "/api/v1/payments", headers=headers, json=body
            )
            assert (
                replay.status_code == 202
                and replay.json()["payment_id"] == payment_id
            )
            conflict = await client.post(
                "/api/v1/payments",
                headers=headers,
                json=body | {"amount": "43.00"},
            )
            assert conflict.status_code == 409
            assert conflict.headers["X-Request-ID"] == request_id
            assert (
                await client.get("/health", headers=headers)
            ).status_code == 200
            print(
                json.dumps(
                    {
                        "create": 202,
                        "swagger": 200,
                        "openapi": 200,
                        "get": 200,
                        "conflict": 409,
                        "payment_status": state["payment_status"],
                        "outbox_published": True,
                        "webhook_delivered": True,
                        "same_payment_on_replay": True,
                        "request_id": request_id,
                        "payment_id": payment_id,
                    }
                )
            )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
