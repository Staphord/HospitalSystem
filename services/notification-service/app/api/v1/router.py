import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    MarkReadResponse,
    NotificationCreateRequest,
    NotificationItemResponse,
    NotificationListResponse,
    NotificationPreferenceResponse,
    UnreadCountResponse,
    UpdatePreferenceRequest,
)
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db
from app.services.broadcaster import broadcaster
from app.services import notifications as notification_service

router = APIRouter(dependencies=[Depends(get_current_tenant)])


@router.get("", response_model=NotificationListResponse, tags=["Notifications"])
async def list_notifications(
    unread_only: bool = Query(False),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> NotificationListResponse:
    """List paginated notifications for the current user and role."""
    tenant_id = ctx.tenant_id or "default"
    return await notification_service.get_user_notifications(
        db,
        tenant_id=tenant_id,
        recipient_id=ctx.user_sub,
        recipient_role=ctx.roles,
        unread_only=unread_only,
        category=category,
        page=page,
        page_size=page_size,
    )


@router.get("/unread-count", response_model=UnreadCountResponse, tags=["Notifications"])
async def get_unread_notification_count(
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> UnreadCountResponse:
    """Fetch unread notification counter."""
    tenant_id = ctx.tenant_id or "default"
    return await notification_service.get_unread_count(
        db,
        tenant_id=tenant_id,
        recipient_id=ctx.user_sub,
        recipient_role=ctx.roles,
    )


@router.patch("/{notification_id}/read", response_model=MarkReadResponse, tags=["Notifications"])
async def mark_single_notification_read(
    notification_id: UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> MarkReadResponse:
    """Mark a single notification record as read."""
    tenant_id = ctx.tenant_id or "default"
    return await notification_service.mark_notification_read(
        db,
        tenant_id=tenant_id,
        notification_id=notification_id,
    )


@router.post("/mark-all-read", response_model=MarkReadResponse, tags=["Notifications"])
async def mark_all_notifications_read(
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> MarkReadResponse:
    """Mark all unread notifications as read for current user."""
    tenant_id = ctx.tenant_id or "default"
    return await notification_service.mark_all_notifications_read(
        db,
        tenant_id=tenant_id,
        recipient_id=ctx.user_sub,
        recipient_role=ctx.roles,
    )



@router.post("", response_model=NotificationItemResponse, status_code=201, tags=["Notifications"])
async def create_notification(
    payload: NotificationCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
) -> NotificationItemResponse:
    """Create a new notification entry and publish live event."""
    result = await notification_service.create_notification(db, payload)
    await broadcaster.broadcast(payload.tenant_id, result.model_dump())
    return result


@router.get("/stream", tags=["Notifications"])
async def stream_notifications(
    ctx: TenantContext = Depends(get_current_tenant),
) -> StreamingResponse:
    """Establish real-time Server-Sent Events stream."""
    tenant_id = ctx.tenant_id or "default"

    async def event_generator():
        queue = broadcaster.subscribe(tenant_id)
        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            broadcaster.unsubscribe(tenant_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/preferences", response_model=NotificationPreferenceResponse, tags=["Preferences"])
async def get_notification_preferences(
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> NotificationPreferenceResponse:
    """Fetch user notification preferences."""
    return await notification_service.get_user_preferences(db, user_id=ctx.user_sub)


@router.put("/preferences", response_model=NotificationPreferenceResponse, tags=["Preferences"])
async def update_notification_preferences(
    payload: UpdatePreferenceRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
) -> NotificationPreferenceResponse:
    """Update user notification delivery preferences."""
    return await notification_service.update_user_preferences(db, user_id=ctx.user_sub, payload=payload)
