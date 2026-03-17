"""Portal notifications: list and mark read."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_customer import get_current_customer
from app.core.database import get_db
from app.models.customer_user import CustomerUser
from app.models.notification import Notification

router = APIRouter(prefix="/notifications", tags=["portal-notifications"])


class NotificationResponse(BaseModel):
    """Notification response."""

    id: int
    channel: str
    title: str
    body: str | None
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[NotificationResponse])
async def list_my_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
):
    """List notifications for current customer user."""
    r = await db.execute(
        select(Notification)
        .where(Notification.customer_user_id == current.id)
        .order_by(Notification.id.desc())
        .limit(100)
    )
    return list(r.scalars().all())


@router.patch("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
):
    """Mark notification as read."""
    r = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.customer_user_id == current.id,
        )
    )
    n = r.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.read_at = datetime.now(timezone.utc)
    await db.flush()
