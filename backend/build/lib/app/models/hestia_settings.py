"""Hestia settings per org: grace period, default package, auto-suspend."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HestiaSettings(Base):
    """Org-level Hestia provisioning settings (grace period, package, auto-suspend)."""

    __tablename__ = "hestia_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True, unique=True)
    grace_period_days: Mapped[int] = mapped_column(Integer, default=5)
    default_hestia_package: Mapped[str | None] = mapped_column(String(128), default="default")
    auto_suspend_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
