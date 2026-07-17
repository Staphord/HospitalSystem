"""Subscription request / approval workflow business logic.

Tenant hospital admins submit requests (plan change or cancellation) which
a super admin must approve or reject. The request state lives on the Tenant
model via pending_action and related columns, so no new table is needed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.master import Tenant
from app.services.subscription_plans import (
    BillingCycle,
    SubscriptionPlan,
    is_upgrade,
    is_downgrade,
    valid_plan_transition,
)
from app.services import subscription_service

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _audit_event(
    db: Session,
    tenant_id: str,
    event_type: str,
    actor_id: str | None,
    actor_type: str,
    reason: str | None,
) -> None:
    try:
        from app.models.saas import SubscriptionAuditLog
        from sqlalchemy import inspect

        if not inspect(db.connection()).has_table(SubscriptionAuditLog.__tablename__):
            return
        parsed_actor_id = None
        if actor_id:
            try:
                parsed_actor_id = uuid.UUID(actor_id) if isinstance(actor_id, str) else actor_id
            except ValueError:
                parsed_actor_id = None

        log = SubscriptionAuditLog(
            tenant_id=tenant_id,
            event_type=event_type,
            actor_id=parsed_actor_id,
            actor_type=actor_type,
            reason=reason,
        )
        db.add(log)
    except Exception:
        logger.exception("Failed to write subscription audit event %s", event_type)


def create_plan_change_request(
    db: Session,
    tenant_id: str,
    action: str,
    requested_plan: str,
    reason: str,
    requested_billing_cycle: str | None = None,
    requested_effective_at_end: bool | None = None,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> Tenant:
    """Submit a plan change request (upgrade or downgrade) for super admin approval.

    Sets pending_action on the tenant so the current subscription state is not
    modified until a super admin approves.
    """
    tenant = _require_tenant(db, tenant_id)
    now = _utc_now()

    if tenant.pending_action:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {tenant.pending_action} request is already pending for this tenant. Resolve it first.",
        )

    try:
        new_plan = SubscriptionPlan(requested_plan)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid plan: {requested_plan}")

    current_plan = SubscriptionPlan(tenant.subscription_plan)

    if not valid_plan_transition(current_plan, new_plan):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan transition from {current_plan.value} to {new_plan.value}.",
        )

    if action == "upgrade" and not is_upgrade(current_plan, new_plan):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"'{new_plan.value}' is not higher than current plan '{current_plan.value}'.",
        )

    if action == "downgrade" and not is_downgrade(current_plan, new_plan):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"'{new_plan.value}' is not lower than current plan '{current_plan.value}'.",
        )

    tenant.pending_action = action
    tenant.requested_plan = new_plan.value
    tenant.request_reason = reason.strip() if reason else None
    tenant.requested_at = now
    tenant.reviewed_by = None
    tenant.reviewed_at = None
    tenant.review_notes = None

    # Save target cycle, timing, and log entry in metadata
    import copy
    meta = copy.deepcopy(tenant.subscription_metadata or {})
    if requested_billing_cycle:
        meta["requested_billing_cycle"] = requested_billing_cycle
    if requested_effective_at_end is not None:
        meta["requested_effective_at_end"] = requested_effective_at_end

    request_id = str(uuid.uuid4())
    history = list(meta.get("requests_history") or [])
    new_req = {
        "request_id": request_id,
        "tenant_id": tenant.tenant_id,
        "hospital_name": tenant.hospital_name,
        "pending_action": action,
        "requested_plan": new_plan.value,
        "request_reason": reason.strip() if reason else None,
        "requested_at": now.isoformat(),
        "status": "pending",
        "billing_cycle": requested_billing_cycle,
        "effective_at_end": requested_effective_at_end,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
    }
    history.append(new_req)
    meta["requests_history"] = history
    tenant.subscription_metadata = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tenant, "subscription_metadata")

    _audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type=f"{action}_requested",
        actor_id=user_sub,
        actor_type="hospital_admin",
        reason=f"Requested {action} from {current_plan.value} to {new_plan.value}: {reason}" if reason else f"Requested {action} from {current_plan.value} to {new_plan.value}",
    )

    log_action(
        db,
        tenant_id,
        f"subscription.{action}_requested",
        {
            "from_plan": current_plan.value,
            "to_plan": new_plan.value,
            "reason": reason,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return tenant


def create_cancellation_request(
    db: Session,
    tenant_id: str,
    reason: str,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> Tenant:
    """Submit a cancellation request for super admin approval."""
    tenant = _require_tenant(db, tenant_id)
    now = _utc_now()

    if tenant.pending_action:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {tenant.pending_action} request is already pending. Resolve it first.",
        )

    tenant.pending_action = "cancellation"
    tenant.requested_plan = None
    tenant.request_reason = reason.strip() if reason else None
    tenant.requested_at = now
    tenant.reviewed_by = None
    tenant.reviewed_at = None
    tenant.review_notes = None

    # Save cancellation details in metadata history
    import copy
    meta = copy.deepcopy(tenant.subscription_metadata or {})
    request_id = str(uuid.uuid4())
    history = list(meta.get("requests_history") or [])
    new_req = {
        "request_id": request_id,
        "tenant_id": tenant.tenant_id,
        "hospital_name": tenant.hospital_name,
        "pending_action": "cancellation",
        "requested_plan": None,
        "request_reason": reason.strip() if reason else None,
        "requested_at": now.isoformat(),
        "status": "pending",
        "billing_cycle": None,
        "effective_at_end": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_notes": None,
    }
    history.append(new_req)
    meta["requests_history"] = history
    tenant.subscription_metadata = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tenant, "subscription_metadata")

    _audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="cancellation_requested",
        actor_id=user_sub,
        actor_type="hospital_admin",
        reason=reason.strip() if reason else None,
    )

    log_action(
        db,
        tenant_id,
        "subscription.cancellation_requested",
        {"reason": reason},
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return tenant


def approve_request(
    db: Session,
    tenant_id: str,
    reviewer_sub: str | None = None,
    notes: str | None = None,
    ip_address: str | None = None,
) -> Tenant:
    """Approve a pending request and execute the actual state transition."""
    tenant = _require_tenant(db, tenant_id)

    if not tenant.pending_action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending request found for this tenant.",
        )

    now = _utc_now()
    action = tenant.pending_action

    metadata = tenant.subscription_metadata or {}
    cycle_str = metadata.get("requested_billing_cycle")
    requested_cycle = BillingCycle(cycle_str) if cycle_str else None
    req_effective_at_end = metadata.get("requested_effective_at_end")

    if action == "cancellation":
        subscription_service.terminate_tenant(
            db=db,
            tenant_id=tenant_id,
            reason=tenant.request_reason or "Cancellation request approved",
            user_sub=reviewer_sub,
            ip_address=ip_address,
        )
    elif action == "upgrade":
        new_plan = SubscriptionPlan(tenant.requested_plan)
        effective = req_effective_at_end if req_effective_at_end is not None else False
        subscription_service.upgrade_subscription(
            db=db,
            tenant_id=tenant_id,
            new_plan=new_plan,
            billing_cycle=requested_cycle,
            effective_at_end=effective,
            user_sub=reviewer_sub,
            ip_address=ip_address,
        )
    elif action == "downgrade":
        new_plan = SubscriptionPlan(tenant.requested_plan)
        effective = req_effective_at_end if req_effective_at_end is not None else True
        subscription_service.downgrade_subscription(
            db=db,
            tenant_id=tenant_id,
            new_plan=new_plan,
            billing_cycle=requested_cycle,
            effective_at_end=effective,
            user_sub=reviewer_sub,
            ip_address=ip_address,
        )

    _clear_pending_request(db, tenant, reviewer_sub, action, notes, is_approval=True)

    tenant.reviewed_at = now
    tenant.review_notes = notes.strip() if notes else None

    _audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type=f"{action}_approved",
        actor_id=reviewer_sub,
        actor_type="super_admin",
        reason=notes.strip() if notes else None,
    )

    log_action(
        db,
        tenant_id,
        f"subscription.{action}_approved",
        {"reviewer": reviewer_sub, "notes": notes},
        user_sub=reviewer_sub,
        ip_address=ip_address,
    )

    return tenant


def reject_request(
    db: Session,
    tenant_id: str,
    reviewer_sub: str | None = None,
    notes: str | None = None,
    ip_address: str | None = None,
) -> Tenant:
    """Reject a pending request without executing any state transition."""
    tenant = _require_tenant(db, tenant_id)

    if not tenant.pending_action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending request found for this tenant.",
        )

    action = tenant.pending_action
    _clear_pending_request(db, tenant, reviewer_sub, action, notes, is_approval=False)

    tenant.review_notes = notes.strip() if notes else None

    _audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type=f"{action}_rejected",
        actor_id=reviewer_sub,
        actor_type="super_admin",
        reason=notes.strip() if notes else None,
    )

    log_action(
        db,
        tenant_id,
        f"subscription.{action}_rejected",
        {"reviewer": reviewer_sub, "notes": notes},
        user_sub=reviewer_sub,
        ip_address=ip_address,
    )

    return tenant


def get_pending_request(tenant: Tenant) -> dict[str, Any]:
    """Return the pending request state for a tenant."""
    if not tenant.pending_action:
        return {}
    return {
        "tenant_id": tenant.tenant_id,
        "hospital_name": tenant.hospital_name,
        "pending_action": tenant.pending_action,
        "requested_plan": tenant.requested_plan,
        "request_reason": tenant.request_reason,
        "requested_at": tenant.requested_at.isoformat() if tenant.requested_at else None,
        "reviewed_by": str(tenant.reviewed_by) if tenant.reviewed_by else None,
        "reviewed_at": tenant.reviewed_at.isoformat() if tenant.reviewed_at else None,
        "review_notes": tenant.review_notes,
    }


def list_pending_requests(
    db: Session,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """List all tenants with a pending request (optionally filtered by tenant_id)."""
    query = db.query(Tenant).filter(
        Tenant.pending_action.isnot(None),
        Tenant.pending_action != "",
    )
    if tenant_id:
        query = query.filter(Tenant.tenant_id == tenant_id)
    tenants = query.all()
    return [get_pending_request(t) for t in tenants]


def list_subscription_requests(
    db: Session,
    tenant_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all subscription requests (pending, approved, rejected)."""
    query = db.query(Tenant)
    if tenant_id:
        query = query.filter(Tenant.tenant_id == tenant_id)
    tenants = query.all()

    all_requests = []
    for t in tenants:
        meta = t.subscription_metadata or {}
        history = meta.get("requests_history") or []

        # Add historical requests
        for req in history:
            req_status = req.get("status", "pending")
            if status and req_status != status:
                continue

            all_requests.append({
                "request_id": req.get("request_id"),
                "tenant_id": t.tenant_id,
                "hospital_name": t.hospital_name,
                "pending_action": req.get("pending_action"),
                "requested_plan": req.get("requested_plan"),
                "request_reason": req.get("request_reason"),
                "requested_at": req.get("requested_at"),
                "reviewed_by": req.get("reviewed_by"),
                "reviewed_at": req.get("reviewed_at"),
                "review_notes": req.get("review_notes"),
                "status": req_status,
                "billing_cycle": req.get("billing_cycle"),
                "effective_at_end": req.get("effective_at_end"),
            })

        # Add pending request if not already logged in history
        if t.pending_action:
            has_pending_in_history = any(
                r.get("status") == "pending" and r.get("pending_action") == t.pending_action
                for r in history
            )
            if not has_pending_in_history:
                if status and status != "pending":
                    continue
                all_requests.append({
                    "request_id": None,
                    "tenant_id": t.tenant_id,
                    "hospital_name": t.hospital_name,
                    "pending_action": t.pending_action,
                    "requested_plan": t.requested_plan,
                    "request_reason": t.request_reason,
                    "requested_at": t.requested_at.isoformat() if t.requested_at else None,
                    "reviewed_by": str(t.reviewed_by) if t.reviewed_by else None,
                    "reviewed_at": t.reviewed_at.isoformat() if t.reviewed_at else None,
                    "review_notes": t.review_notes,
                    "status": "pending",
                    "billing_cycle": meta.get("requested_billing_cycle"),
                    "effective_at_end": meta.get("requested_effective_at_end"),
                })

    # Sort results by requested_at descending
    all_requests.sort(key=lambda r: r.get("requested_at") or "", reverse=True)
    return all_requests


def _clear_pending_request(
    db: Session,
    tenant: Tenant,
    reviewer_sub: str | None,
    action: str,
    notes: str | None,
    is_approval: bool,
) -> None:
    """Clear pending request fields after approval or rejection."""
    parsed_reviewer_sub = None
    if reviewer_sub:
        try:
            parsed_reviewer_sub = uuid.UUID(reviewer_sub) if isinstance(reviewer_sub, str) else reviewer_sub
        except ValueError:
            parsed_reviewer_sub = None

    tenant.pending_action = None
    tenant.requested_plan = None
    tenant.request_reason = None
    tenant.requested_at = None
    tenant.reviewed_by = parsed_reviewer_sub
    tenant.reviewed_at = _utc_now()
    tenant.review_notes = notes.strip() if notes else None

    # Clear requested billing cycle/timing configurations from metadata and update history status
    import copy
    meta = copy.deepcopy(tenant.subscription_metadata or {})
    meta.pop("requested_billing_cycle", None)
    meta.pop("requested_effective_at_end", None)

    history = list(meta.get("requests_history") or [])
    updated = False
    now_iso = _utc_now().isoformat()
    for req in reversed(history):
        if req.get("status") == "pending" and req.get("pending_action") == action:
            req["status"] = "approved" if is_approval else "rejected"
            req["reviewed_by"] = str(reviewer_sub) if reviewer_sub else None
            req["reviewed_at"] = now_iso
            req["review_notes"] = notes.strip() if notes else None
            updated = True
            break

    if not updated and history:
        history[-1]["status"] = "approved" if is_approval else "rejected"
        history[-1]["reviewed_by"] = str(reviewer_sub) if reviewer_sub else None
        history[-1]["reviewed_at"] = now_iso
        history[-1]["review_notes"] = notes.strip() if notes else None

    meta["requests_history"] = history
    tenant.subscription_metadata = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tenant, "subscription_metadata")


def log_action(
    db: Session,
    tenant_id: str,
    action: str,
    detail: dict[str, Any],
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist an auditable event to global_audit_logs."""
    try:
        from app.models.master import GlobalAuditLog
        import json

        log = GlobalAuditLog(
            tenant_id=tenant_id,
            user_sub=user_sub,
            action=action,
            detail=json.dumps(detail, default=str),
            ip_address=ip_address,
        )
        db.add(log)
    except Exception:
        logger.exception("Failed to write audit log for %s", action)
