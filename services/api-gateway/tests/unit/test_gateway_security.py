import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_invalid_bearer_token_is_rejected_by_gateway():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/reception/patients",
            headers={"Authorization": "Bearer invalid_token"},
        )

    assert response.status_code == 401

