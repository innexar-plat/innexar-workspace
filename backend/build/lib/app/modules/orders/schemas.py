"""Orders and briefings API schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class OrderItem(BaseModel):
    """Single order (paid invoice for site product) with project if created."""

    invoice_id: int
    customer_id: int
    customer_name: str
    product_name: str
    subscription_id: int
    project_id: int | None
    project_status: str | None
    status: str  # aguardando_briefing | briefing_recebido | etc
    total: float
    currency: str
    paid_at: datetime | None
    created_at: datetime


class BriefingItem(BaseModel):
    """Briefing (project request) submitted by customer."""

    id: int
    customer_id: int
    customer_name: str
    project_id: int | None
    project_name: str
    project_type: str
    description: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BriefingDetail(BriefingItem):
    """Full briefing detail including meta (site briefing fields)."""

    meta: dict[str, Any] | None = None
    budget: str | None = None
    timeline: str | None = None
