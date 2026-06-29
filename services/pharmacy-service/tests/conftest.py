import pytest
from fastapi.testclient import TestClient

from app.core.security import TokenPayload, get_current_active_user
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.main import app

PHARMACIST_USER = TokenPayload(
    sub="test-pharmacist-sub",
    preferred_username="pharmacist.test",
    email="pharmacist@test.com",
    realm_access={"roles": ["pharmacist"]},
    raw={"sub": "test-pharmacist-sub", "realm_access": {"roles": ["pharmacist"]}},
)

TENANT_CTX = TenantContext(
    tenant_id="default-hospital",
    user_sub="test-pharmacist-sub",
    preferred_username="pharmacist.test",
    email="pharmacist@test.com",
    roles=["pharmacist"],
    is_super_admin=False,
)


async def _override_tenant() -> TenantContext:
    return TENANT_CTX


async def _override_user() -> TokenPayload:
    return PHARMACIST_USER


@pytest.fixture
def pharmacist_client() -> TestClient:
    app.dependency_overrides[get_current_tenant] = _override_tenant
    app.dependency_overrides[get_current_active_user] = _override_user
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
