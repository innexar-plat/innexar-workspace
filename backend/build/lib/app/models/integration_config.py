"""IntegrationConfig: provider config with encrypted value."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.customer import Customer


class IntegrationConfig(Base):
    """Integration config (Stripe, MP, etc.) with encrypted secret."""

    __tablename__ = "integration_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="innexar", index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # global | tenant | customer
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="test")  # test | live
    enabled: Mapped[bool] = mapped_column(default=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer: Mapped["Customer | None"] = relationship("Customer")
