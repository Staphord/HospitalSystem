from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core.config import settings


def _issuer() -> str:
    return f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"


def create_impersonation_token(
    super_admin_sub: str,
    super_admin_username: str,
    target_tenant_id: str,
) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": super_admin_sub,
        "preferred_username": super_admin_username,
        "tenant_id": target_tenant_id,
        "scope": "readonly",
        "impersonator": True,
        "impersonation": True,
        "realm_access": {"roles": ["hospital_admin"]},
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.impersonation_token_ttl)).timestamp()),
        "jti": str(uuid.uuid4()),
        "iss": _issuer(),
    }

    token = jwt.encode(
        payload,
        settings.secret_key,
        algorithm="HS256",
        headers={"kid": "impersonation-key"},
    )

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": settings.impersonation_token_ttl,
        "scope": "readonly",
        "tenant_id": target_tenant_id,
        "impersonator": True,
    }


async def log_impersonation_event(
    action: str,
    super_admin_sub: str,
    target_tenant_id: str,
    ip_address: str | None = None,
) -> None:
    from app.db.master import get_master_db
    from app.models.master import GlobalAuditLog

    db = get_master_db()
    try:
        record = GlobalAuditLog(
            tenant_id=target_tenant_id,
            user_sub=super_admin_sub,
            action=action,
            detail=f"Super admin impersonation {action} for tenant {target_tenant_id}",
            ip_address=ip_address,
        )
        db.add(record)
        db.commit()
    finally:
        db.close()
