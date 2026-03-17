"""Notifications service: create notification and optionally send email."""
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks

from app.models.notification import Notification
from app.providers.email.loader import get_email_provider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def create_notification_and_maybe_send_email(
    db: "AsyncSession",
    background_tasks: BackgroundTasks,
    *,
    customer_user_id: int | None = None,
    user_id: int | None = None,
    channel: str = "in_app",
    title: str = "",
    body: str = "",
    recipient_email: str | None = None,
    org_id: str = "innexar",
) -> Notification:
    """Create a Notification and, if channel includes 'email' and provider is configured, send email in background."""
    n = Notification(
        customer_user_id=customer_user_id,
        user_id=user_id,
        channel=channel,
        title=title,
        body=body,
    )
    db.add(n)
    await db.flush()
    if "email" in channel and recipient_email:
        provider = await get_email_provider(db, org_id=org_id)
        if provider:
            background_tasks.add_task(
                provider.send,
                recipient_email,
                title,
                body or "",
                None,
            )
    return n
