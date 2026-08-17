from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
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
from app.exceptions import NotFoundError
from app.models.notifications import Notification, NotificationPreference


def _build_recipient_filter(recipient_id: str, recipient_roles: list[str] | str | None):
    roles = []
    if isinstance(recipient_roles, str):
        roles = [recipient_roles.lower()]
    elif isinstance(recipient_roles, (list, tuple, set)):
        roles = [str(r).lower() for r in recipient_roles if r]

    if roles:
        return or_(
            Notification.recipient_id == recipient_id,
            and_(Notification.recipient_id.is_(None), func.lower(Notification.recipient_role).in_(roles)),
            and_(Notification.recipient_id.is_(None), Notification.recipient_role.is_(None)),
        )
    return or_(
        Notification.recipient_id == recipient_id,
        and_(Notification.recipient_id.is_(None), Notification.recipient_role.is_(None)),
    )


async def get_user_notifications(
    db: AsyncSession,
    tenant_id: str,
    recipient_id: str,
    recipient_role: list[str] | str | None = None,
    unread_only: bool = False,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> NotificationListResponse:
    """Fetch notifications targeted to a recipient user or recipient role."""
    recipient_filter = _build_recipient_filter(recipient_id, recipient_role)

    roles = []
    if isinstance(recipient_role, str):
        roles = [recipient_role.lower()]
    elif isinstance(recipient_role, (list, tuple, set)):
        roles = [str(r).lower() for r in recipient_role if r]

    is_super = "superadmin" in roles or "super_admin" in roles

    if is_super or tenant_id == "default":
        conditions = [recipient_filter]
    else:
        conditions = [
            Notification.tenant_id == tenant_id,
            recipient_filter,
        ]

    if unread_only:
        conditions.append(Notification.status == "unread")
    if category:
        conditions.append(Notification.category == category)

    count_stmt = select(func.count(Notification.notification_id)).where(*conditions)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    unread_conditions = list(conditions)
    if not unread_only:
        unread_conditions.append(Notification.status == "unread")

    unread_count_stmt = select(func.count(Notification.notification_id)).where(*unread_conditions)
    unread_res = await db.execute(unread_count_stmt)
    unread_total = unread_res.scalar() or 0

    offset = (page - 1) * page_size
    stmt = (
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    res = await db.execute(stmt)
    notifications = res.scalars().all()

    items = [
        NotificationItemResponse(
            notification_id=n.notification_id,
            tenant_id=n.tenant_id,
            recipient_id=n.recipient_id,
            recipient_role=n.recipient_role,
            title=n.title,
            message=n.message,
            category=n.category,
            priority=n.priority,
            status=n.status,
            action_url=n.action_url,
            metadata_payload=n.metadata_payload,
            read_at=n.read_at,
            created_at=n.created_at,
        )
        for n in notifications
    ]

    return NotificationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        unread_count=unread_total,
    )


async def get_unread_count(
    db: AsyncSession,
    tenant_id: str,
    recipient_id: str,
    recipient_role: list[str] | str | None = None,
) -> UnreadCountResponse:
    """Calculate total unread notifications for a user or role."""
    pref_stmt = select(NotificationPreference).where(NotificationPreference.user_id == recipient_id)
    pref_res = await db.execute(pref_stmt)
    pref = pref_res.scalar_one_or_none()
    if pref and not pref.in_app_enabled:
        return UnreadCountResponse(unread_count=0)

    recipient_filter = _build_recipient_filter(recipient_id, recipient_role)

    roles = []
    if isinstance(recipient_role, str):
        roles = [recipient_role.lower()]
    elif isinstance(recipient_role, (list, tuple, set)):
        roles = [str(r).lower() for r in recipient_role if r]

    is_super = "superadmin" in roles or "super_admin" in roles

    if is_super or tenant_id == "default":
        conditions = [recipient_filter, Notification.status == "unread"]
    else:
        conditions = [
            Notification.tenant_id == tenant_id,
            recipient_filter,
            Notification.status == "unread",
        ]

    unread_count_stmt = select(func.count(Notification.notification_id)).where(*conditions)
    unread_res = await db.execute(unread_count_stmt)
    unread_total = unread_res.scalar() or 0

    return UnreadCountResponse(unread_count=unread_total)


async def mark_notification_read(
    db: AsyncSession,
    tenant_id: str,
    notification_id: UUID,
) -> MarkReadResponse:
    """Mark a single notification as read."""
    notification = await db.get(Notification, notification_id)
    if not notification or notification.tenant_id != tenant_id:
        raise NotFoundError("Notification record not found")

    if notification.status != "read":
        notification.status = "read"
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()

    return MarkReadResponse(notification_id=notification_id, marked_count=1, status="read")


async def mark_all_notifications_read(
    db: AsyncSession,
    tenant_id: str,
    recipient_id: str,
    recipient_role: list[str] | str | None = None,
) -> MarkReadResponse:
    """Mark all unread notifications as read for a recipient."""
    recipient_filter = _build_recipient_filter(recipient_id, recipient_role)

    roles = []
    if isinstance(recipient_role, str):
        roles = [recipient_role.lower()]
    elif isinstance(recipient_role, (list, tuple, set)):
        roles = [str(r).lower() for r in recipient_role if r]

    is_super = "superadmin" in roles or "super_admin" in roles

    if is_super or tenant_id == "default":
        conditions = [recipient_filter, Notification.status == "unread"]
    else:
        conditions = [
            Notification.tenant_id == tenant_id,
            recipient_filter,
            Notification.status == "unread",
        ]

    stmt = select(Notification).where(*conditions)
    res = await db.execute(stmt)
    unread_items = res.scalars().all()

    now = datetime.now(timezone.utc)
    for item in unread_items:
        item.status = "read"
        item.read_at = now

    if unread_items:
        await db.commit()

    return MarkReadResponse(notification_id=None, marked_count=len(unread_items), status="read")



async def create_notification(
    db: AsyncSession,
    payload: NotificationCreateRequest,
) -> NotificationItemResponse:
    """Persist a new notification record."""
    notification = Notification(
        notification_id=uuid4(),
        tenant_id=payload.tenant_id,
        recipient_id=payload.recipient_id,
        recipient_role=payload.recipient_role,
        title=payload.title,
        message=payload.message,
        category=payload.category,
        priority=payload.priority,
        action_url=payload.action_url,
        metadata_payload=payload.metadata_payload,
        status="unread",
        created_at=datetime.now(timezone.utc),
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    return NotificationItemResponse(
        notification_id=notification.notification_id,
        tenant_id=notification.tenant_id,
        recipient_id=notification.recipient_id,
        recipient_role=notification.recipient_role,
        title=notification.title,
        message=notification.message,
        category=notification.category,
        priority=notification.priority,
        status=notification.status,
        action_url=notification.action_url,
        metadata_payload=notification.metadata_payload,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


async def get_user_preferences(
    db: AsyncSession,
    user_id: str,
) -> NotificationPreferenceResponse:
    """Retrieve notification channel preferences for a user."""
    stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    res = await db.execute(stmt)
    pref = res.scalar_one_or_none()

    if not pref:
        return NotificationPreferenceResponse(
            user_id=user_id,
            in_app_enabled=True,
            email_enabled=True,
            sms_enabled=False,
            categories_disabled=[],
        )

    return NotificationPreferenceResponse(
        user_id=pref.user_id,
        in_app_enabled=pref.in_app_enabled,
        email_enabled=pref.email_enabled,
        sms_enabled=pref.sms_enabled,
        categories_disabled=pref.categories_disabled or [],
    )


async def update_user_preferences(
    db: AsyncSession,
    user_id: str,
    payload: UpdatePreferenceRequest,
) -> NotificationPreferenceResponse:
    """Update notification channel preferences for a user."""
    stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    res = await db.execute(stmt)
    pref = res.scalar_one_or_none()

    if not pref:
        pref = NotificationPreference(
            preference_id=uuid4(),
            user_id=user_id,
            in_app_enabled=True if payload.in_app_enabled is None else payload.in_app_enabled,
            email_enabled=True if payload.email_enabled is None else payload.email_enabled,
            sms_enabled=False if payload.sms_enabled is None else payload.sms_enabled,
            categories_disabled=payload.categories_disabled or [],
        )
        db.add(pref)
    else:
        if payload.in_app_enabled is not None:
            pref.in_app_enabled = payload.in_app_enabled
        if payload.email_enabled is not None:
            pref.email_enabled = payload.email_enabled
        if payload.sms_enabled is not None:
            pref.sms_enabled = payload.sms_enabled
        if payload.categories_disabled is not None:
            pref.categories_disabled = payload.categories_disabled
        pref.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(pref)

    return NotificationPreferenceResponse(
        user_id=pref.user_id,
        in_app_enabled=pref.in_app_enabled,
        email_enabled=pref.email_enabled,
        sms_enabled=pref.sms_enabled,
        categories_disabled=pref.categories_disabled or [],
    )
