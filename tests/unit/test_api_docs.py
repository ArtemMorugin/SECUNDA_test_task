import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc"])
async def test_documentation_uses_api_openapi_schema(path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/api/openapi.json" in response.text
    if path == "/api/docs":
        assert "/api/docs/oauth2-redirect" in response.text


async def test_openapi_documents_api_key_on_all_protected_operations():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    for path, method in (
        ("/api/v1/payments", "post"),
        ("/api/v1/payments/{payment_id}", "get"),
        ("/health", "get"),
    ):
        assert schema["paths"][path][method]["security"] == [
            {"APIKeyHeader": []}
        ]
    assert "integration-test-key" not in response.text
