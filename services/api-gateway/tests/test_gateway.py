import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "api-gateway"


@pytest.mark.asyncio
async def test_proxy_no_auth_rejected(client):
    response = await client.get("/api/v1/reception/patients")
    # Missing auth should be rejected by middleware
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_proxy_invalid_token_rejected(client):
    response = await client.get(
        "/api/v1/reception/patients",
        headers={"Authorization": "Bearer invalid_token"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

