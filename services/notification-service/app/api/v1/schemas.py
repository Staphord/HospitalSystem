from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NotificationCategory = Literal["clinical", "billing", "pharmacy", "system"]
NotificationPriority = Literal["low", "normal", "urgent", "emergency"]
NotificationStatus = Literal["unread", "read"]


class NotificationCreateRequest(BaseModel):
    """Schema for creating a new notification."""

    tenant_id: str = Field(..., max_length=50)
    recipient_id: str | None = Field(None, max_length=100)
    recipient_role: str | None = Field(None, max_length=50)
    title: str = Field(..., max_length=255)
    message: str
    category: NotificationCategory = "system"
    priority: NotificationPriority = "normal"
    action_url: str | None = Field(None, max_length=500)
    metadata_payload: dict[str, Any] | None = None


class NotificationItemResponse(BaseModel):
    """Schema for returning a single notification."""

    model_config = ConfigDict(from_attributes=True)

    notification_id: UUID
    tenant_id: str
    recipient_id: str | None
    recipient_role: str | None
    title: str
    message: str
    category: NotificationCategory
    priority: NotificationPriority
    status: NotificationStatus
    action_url: str | None
    metadata_payload: dict[str, Any] | None
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    """Schema for paginated notification items."""

    items: list[NotificationItemResponse]
    total: int
    page: int
    page_size: int
    unread_count: int


class UnreadCountResponse(BaseModel):
    """Schema for returning unread notification counter."""

    unread_count: int


class MarkReadResponse(BaseModel):
    """Schema for notification mark-as-read acknowledgement."""

    notification_id: UUID | None = None
    marked_count: int
    status: str = "read"


class NotificationPreferenceResponse(BaseModel):
    """Schema for user notification preferences."""

    user_id: str
    in_app_enabled: bool
    email_enabled: bool
    sms_enabled: bool
    categories_disabled: list[str] = Field(default_factory=list)


class UpdatePreferenceRequest(BaseModel):
    """Schema for updating user notification preferences."""

    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    categories_disabled: list[str] | None = None
