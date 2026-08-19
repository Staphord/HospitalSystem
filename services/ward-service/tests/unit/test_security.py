"""Unit tests for require_any_role (multi-role auth gate)."""

import asyncio

import pytest
from fastapi import HTTPException

from app.core.security import TokenPayload, require_any_role


def _payload(roles: list[str]) -> TokenPayload:
    return TokenPayload(
        sub="user-1",
        preferred_username="user-1",
        email=None,
        realm_access={"roles": roles},
        raw={},
    )


def test_require_any_role_allows_matching_role():
    dep = require_any_role("doctor", "clinician")
    result = asyncio.run(dep(user=_payload(["doctor"])))
    assert result.sub == "user-1"


def test_require_any_role_allows_hospital_admin_regardless_of_list():
    dep = require_any_role("doctor", "clinician")
    result = asyncio.run(dep(user=_payload(["hospital_admin"])))
    assert result.sub == "user-1"


def test_require_any_role_allows_super_admin_regardless_of_list():
    dep = require_any_role("doctor", "clinician")
    result = asyncio.run(dep(user=_payload(["super_admin"])))
    assert result.sub == "user-1"


def test_require_any_role_rejects_non_matching_role():
    dep = require_any_role("doctor", "clinician")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(user=_payload(["nurse"])))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_extract_realm_and_token_decoding_exceptions():
    from app.core import security
    from jose import jwt, ExpiredSignatureError

    # Realm extraction invalid token
    assert security._extract_realm_from_iss("invalid.jwt.token") is None

    # Invalid header token decoding
    with pytest.raises(HTTPException) as exc:
        await security._decode_token("invalid.jwt.token")
    assert exc.value.status_code == 401

    # Missing credentials in get_current_active_user
    with pytest.raises(HTTPException) as exc:
        await security.get_current_active_user(request=None, credentials=None)
    assert exc.value.status_code == 401
