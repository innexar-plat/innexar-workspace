"""Projects schemas."""
from datetime import datetime

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    """Create project."""

    customer_id: int
    name: str
    status: str = "active"
    subscription_id: int | None = None


class ProjectUpdate(BaseModel):
    """Update project (partial)."""

    name: str | None = None
    status: str | None = None
    expected_delivery_at: datetime | None = None


class ProjectResponse(BaseModel):
    """Project response."""

    id: int
    org_id: str
    customer_id: int
    name: str
    status: str
    subscription_id: int | None
    expected_delivery_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
