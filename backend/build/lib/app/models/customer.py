"""Customer (portal client) model."""
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.customer_user import CustomerUser
    from app.modules.crm.models import Contact
    from app.modules.projects.models import Project
    from app.modules.support.models import Ticket


class Customer(Base):
    """Customer (portal client)."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    mp_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    users: Mapped[list["CustomerUser"]] = relationship(
        "CustomerUser", back_populates="customer", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["Contact"]] = relationship(
        "Contact", back_populates="customer", foreign_keys="Contact.customer_id"
    )
    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="customer"
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket", back_populates="customer"
    )
