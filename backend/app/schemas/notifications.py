from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp


class Notification(ApiModel):
    id: str
    company_id: str | None
    kind: str
    title: str
    body: str | None
    related_type: str | None
    related_id: str | None
    created_at: Timestamp
    read_at: Timestamp | None


class NotificationList(ApiModel):
    items: list[Notification]
    unread_count: int
    next_cursor: str | None = None


class NotificationsRead(RequestModel):
    ids: list[str] | None = Field(default=None, max_length=500)
    all: bool | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if bool(self.all) == bool(self.ids):
            raise ValueError("send either ids or all: true")
        return self


class NotificationsReadResult(ApiModel):
    marked: int
    unread_count: int


class NotificationPreferences(ApiModel):
    """Added (M8): which kinds are also sent by e-mail (opt-in; needs a company e-mail identity)."""

    email_kinds: list[str]
    email_available: bool


class NotificationPreferencesUpdate(RequestModel):
    email_kinds: list[str] = Field(max_length=10)
