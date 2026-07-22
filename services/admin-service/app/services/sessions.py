"""Hospital active-session listing and revoke (FR-53 sessions dashboard)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.auth import RefreshToken
from app.models.user import User
from app.services import audit_service
from app.services.keycloak_admin import logout_user_sessions


def _device_from_ua(user_agent: str | None) -> str:
    if not user_agent:
        return "Web Browser"
    ua = user_agent.lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "android" in ua:
        return "Android Device"
    if "windows" in ua:
        return "Windows PC"
    if "macintosh" in ua or "mac os x" in ua:
        return "Mac"
    if "linux" in ua:
        return "Linux PC"
    return "Web Browser"


def _refresh_token_columns(master_db: Session) -> set[str]:
    try:
        return {c["name"] for c in inspect(master_db.get_bind()).get_columns("refresh_tokens")}
    except Exception:
        return set()


def list_active_sessions(master_db: Session, tenant_db: Session, tenant_id: str) -> list[dict]:
    users = (
        tenant_db.query(User)
        .filter(User.hospital_id == tenant_id, User.deleted_at.is_(None))
        .all()
    )
    # Also include users whose hospital_id may be unset but belong to this tenant DB
    if not users:
        users = tenant_db.query(User).filter(User.deleted_at.is_(None)).all()

    subs = {u.keycloak_sub for u in users if u.keycloak_sub}
    if not subs:
        return []

    now = datetime.now(timezone.utc)
    cols = _refresh_token_columns(master_db)
    has_meta = "ip_address" in cols and "user_agent" in cols

    if has_meta:
        tokens = (
            master_db.query(RefreshToken)
            .filter(
                RefreshToken.keycloak_sub.in_(list(subs)),
                RefreshToken.is_revoked.is_(False),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
            .all()
        )
        token_rows = [
            {
                "session_id": t.session_id,
                "keycloak_sub": t.keycloak_sub,
                "created_at": t.created_at,
                "ip_address": t.ip_address,
                "user_agent": t.user_agent,
            }
            for t in tokens
        ]
    else:
        # Pre-migration master DB: select only core columns.
        sub_list = list(subs)
        placeholders = ", ".join(f":s{i}" for i in range(len(sub_list)))
        params = {f"s{i}": sub for i, sub in enumerate(sub_list)}
        params["now"] = now
        result = master_db.execute(
            text(
                f"""
                SELECT session_id, keycloak_sub, created_at
                FROM refresh_tokens
                WHERE keycloak_sub IN ({placeholders})
                  AND is_revoked = false
                  AND expires_at > :now
                ORDER BY created_at DESC
                """
            ),
            params,
        )
        token_rows = [
            {
                "session_id": row.session_id,
                "keycloak_sub": row.keycloak_sub,
                "created_at": row.created_at,
                "ip_address": None,
                "user_agent": None,
            }
            for row in result
        ]

    user_map = {u.keycloak_sub: u for u in users}
    out: list[dict] = []
    for token in token_rows:
        user = user_map.get(token["keycloak_sub"])
        if not user:
            continue
        out.append(
            {
                "id": token["session_id"],
                "staff_id": user.keycloak_sub,
                "staff_name": user.full_name or user.username or user.email or "Staff",
                "staff_role": user.role or "hospital_user",
                "department": None,
                "login_time": token["created_at"],
                "device": _device_from_ua(token.get("user_agent")),
                "ip_address": token.get("ip_address") or "—",
                "avatar_url": "",
            }
        )
    return out


async def revoke_session(
    master_db: Session,
    tenant_db: Session,
    *,
    session_id: str,
    tenant_id: str,
    actor_sub: str,
    realm: str,
    ip: str | None = None,
) -> None:
    token = (
        master_db.query(RefreshToken)
        .filter(RefreshToken.session_id == session_id)
        .first()
    )
    if not token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found")

    users = (
        tenant_db.query(User)
        .filter(User.keycloak_sub == token.keycloak_sub, User.deleted_at.is_(None))
        .all()
    )
    if not users:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found for this hospital")

    token.is_revoked = True
    master_db.commit()

    # Invalidate Keycloak SSO sessions for that user (all devices).
    try:
        await logout_user_sessions(token.keycloak_sub, realm=realm)
    except Exception:
        pass

    audit_service.log_change(
        tenant_db,
        user_id=actor_sub,
        action="REVOKE_SESSION",
        table_name="refresh_tokens",
        record_id=session_id,
        new_values={"keycloak_sub": token.keycloak_sub},
        ip_address=ip,
    )
