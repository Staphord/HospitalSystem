import os

import pytest
import httpx
from fastapi import status
from httpx import AsyncClient

from app.main import app


KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
REALM = os.getenv("KEYCLOAK_REALM", "hospital-realm")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "hospital-api")
CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "hospital-api-secret")
TEST_USER = os.getenv("KEYCLOAK_TEST_USERNAME", "testuser")
TEST_PASS = os.getenv("KEYCLOAK_TEST_PASSWORD", "testpassword")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_TEST_USERNAME", "adminuser")
ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_TEST_PASSWORD", "adminpassword")


async def _get_token(username: str, password: str) -> str:
    token_url = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": username,
        "password": password,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(token_url, data=data)
        response.raise_for_status()
        return response.json()["access_token"]


@pytest.mark.asyncio
async def test_me_with_valid_token() -> None:
    token = await _get_token(TEST_USER, TEST_PASS)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_200_OK
        payload = response.json()
        assert payload["sub"]


@pytest.mark.asyncio
async def test_invalid_token_rejected() -> None:
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer invalid"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_admin_role_required_for_create() -> None:
    token = await _get_token(ADMIN_USER, ADMIN_PASS)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/patients/create",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_non_admin_cannot_create_patient() -> None:
    token = await _get_token(TEST_USER, TEST_PASS)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/patients/create",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
