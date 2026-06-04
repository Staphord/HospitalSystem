import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import status

from app.main import app

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "hospital-api")
CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "hospital-api-secret")
TEST_USER = os.getenv("KEYCLOAK_TEST_USERNAME", "testuser")
TEST_PASS = os.getenv("KEYCLOAK_TEST_PASSWORD", "testpassword")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_TEST_USERNAME", "adminuser")
ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_TEST_PASSWORD", "adminpassword")

MOCK_TOKEN_RESPONSE = {
    "access_token": "mock-access-token",
    "refresh_token": "mock-refresh-token",
    "expires_in": 300,
    "refresh_expires_in": 1800,
    "token_type": "Bearer",
    "session_state": "mock-session-id",
    "not-before-policy": 0,
}


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _get_keycloak_token(username: str, password: str) -> str:
    token_url = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": username,
        "password": password,
    }
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.post(token_url, data=data)
        response.raise_for_status()
        return response.json()["access_token"]


# ── Unit tests (mocked Keycloak) ──


@pytest.mark.asyncio
async def test_login_returns_tokens(client) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.is_success = True
    mock_response.json.return_value = MOCK_TOKEN_RESPONSE

    mock_post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient.post", mock_post):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["access_token"] == "mock-access-token"
    assert data["refresh_token"] == "mock-refresh-token"


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(client) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 401

    mock_post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient.post", mock_post):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "wrong", "password": "wrong"},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_invalid_token_rejected(client) -> None:
    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_missing_bearer_token_rejected(client) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_health_endpoint_public(client) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


# ── Integration tests (require running Keycloak) ──


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_login_integration(client) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_refresh_token_integration(client) -> None:
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    refresh_token = login_resp.json()["refresh_token"]

    refresh_resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code == status.HTTP_200_OK
    assert "access_token" in refresh_resp.json()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_logout_integration(client) -> None:
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USER, "password": TEST_PASS},
    )
    data = login_resp.json()
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]

    logout_resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_me_with_valid_token(client) -> None:
    token = await _get_keycloak_token(TEST_USER, TEST_PASS)
    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["sub"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_admin_role_required_for_create(client) -> None:
    token = await _get_keycloak_token(ADMIN_USER, ADMIN_PASS)
    response = await client.post(
        "/api/v1/patients/create",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("KEYCLOAK_URL"),
    reason="Requires running Keycloak — set KEYCLOAK_URL",
)
async def test_non_admin_cannot_create_patient(client) -> None:
    token = await _get_keycloak_token(TEST_USER, TEST_PASS)
    response = await client.post(
        "/api/v1/patients/create",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
