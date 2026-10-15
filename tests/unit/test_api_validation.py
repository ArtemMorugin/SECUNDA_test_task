from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 401),
        ({"X-API-Key": "wrong"}, 401),
        ({"X-API-Key": "integration-test-key"}, 422),
        ({"X-API-Key": "integration-test-key", "Idempotency-Key": ""}, 422),
    ],
)
async def test_post_headers(headers, status):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/payments",
            headers=headers,
            json={
                "amount": "1.00",
                "currency": "USD",
                "description": "test",
                "metadata": {},
                "webhook_url": "https://example.org/webhook",
            },
        )
    assert response.status_code == status


@pytest.mark.parametrize("path", ["/health", f"/api/v1/payments/{uuid4()}"])
async def test_get_requires_key(path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},
        {"amount": "1.234"},
        {"amount": "NaN"},
        {"currency": "GBP"},
        {"description": "   "},
        {"webhook_url": "ftp://example.org"},
    ],
)
async def test_invalid_body(changes):
    body = {
        "amount": "1.00",
        "currency": "RUB",
        "description": "test",
        "webhook_url": "https://example.org",
    } | changes
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/payments",
            headers={
                "X-API-Key": "integration-test-key",
                "Idempotency-Key": "test",
            },
            json=body,
        )
    assert response.status_code == 422
