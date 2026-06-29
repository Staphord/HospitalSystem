import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "wrong", "password": "wrong"},
    )
    # Returns 401 when Keycloak rejects, or 400 when Keycloak is unreachable
    assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_400_BAD_REQUEST, status.HTTP_429_TOO_MANY_REQUESTS)


@pytest.mark.asyncio
async def test_signup_validation(client):
    response = await client.post(
        "/api/v1/auth/signup",
        json={"hospital_name": "", "admin_username": "ab", "admin_password": "short", "admin_email": "not-an-email"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_password_reset_request(client):
    from app.core.database import get_session_local
    from app.models.user import User
    
    db = get_session_local()()
    try:
        # Clean up any existing stale test user to avoid uniqueness conflicts
        existing = db.query(User).filter(User.email == "test@example.com").first()
        if not existing:
            user = User(
                email="test@example.com",
                username="testuser",
                full_name="Test User",
                role="hospital_user",
                keycloak_sub="dummy-sub",
                hospital_id="hosp-citygeneral",
            )
            db.add(user)
            db.commit()
    finally:
        db.close()

    response = await client.post(
        "/api/v1/auth/password-reset",
        json={"email": "test@example.com"},
    )
    assert response.status_code == status.HTTP_202_ACCEPTED



@pytest.mark.asyncio
async def test_refresh_token_validation(client):
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": ""},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_superadmin_login_rejects_invalid_credentials(client):
    response = await client.post(
        "/api/v1/auth/superadmin/login",
        json={"username": "wrong", "password": "wrong"},
    )
    assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_400_BAD_REQUEST, status.HTTP_429_TOO_MANY_REQUESTS)

