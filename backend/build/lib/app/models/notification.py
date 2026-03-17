"""Notification model (in_app / email)."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.customer_user import CustomerUser
    from app.models.user import User


class Notification(Base):
    """Notification for customer_user or staff user."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    customer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_users.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), default="in_app")  # in_app | email
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    user: Mapped["User | None"] = relationship("User")
    customer_user: Mapped["CustomerUser | None"] = relationship("CustomerUser")
