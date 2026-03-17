"""Customer password reset token (portal forgot password)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CustomerPasswordResetToken(Base):
    """One-time token for customer password reset. Expires after 24h."""

    __tablename__ = "customer_password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    customer_user_id: Mapped[int] = mapped_column(
        ForeignKey("customer_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Optional: relationship to CustomerUser if needed
    # customer_user: Mapped["CustomerUser"] = relationship("CustomerUser", back_populates="password_reset_tokens")
