"""Staff login history from master global_audit_logs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.master import GlobalAuditLog


LOGIN_ACTIONS = {"LOGIN", "LOGOUT", "FIRST_LOGIN_PASSWORD_ESTABLISHED"}


def list_login_history(
    master_db: Session,
    *,
    user_sub: str,
    tenant_id: str | None = None,
    days: int = 30,
    limit: int = 50,
) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = master_db.query(GlobalAuditLog).filter(
        GlobalAuditLog.user_sub == user_sub,
        GlobalAuditLog.action.in_(list(LOGIN_ACTIONS)),
        GlobalAuditLog.created_at >= since,
    )
    if tenant_id:
        q = q.filter(
            (GlobalAuditLog.tenant_id == tenant_id) | (GlobalAuditLog.tenant_id.is_(None))
        )
    rows = q.order_by(GlobalAuditLog.created_at.desc()).limit(limit).all()

    out: list[dict] = []
    for row in rows:
        status = "Success"
        if row.action == "LOGOUT":
            status = "Expired"
        elif "fail" in (row.detail or "").lower():
            status = "Failed"
        out.append(
            {
                "timestamp": row.created_at,
                "ip": row.ip_address,
                "device": "Web Browser",
                "duration": "—",
                "workspace": "Hospital Portal",
                "status": status,
                "detail": row.detail,
            }
        )
    return out
