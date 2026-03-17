"""CRM schemas."""
from datetime import datetime

from pydantic import BaseModel


class ContactCreate(BaseModel):
    """Create contact."""

    name: str
    email: str | None = None
    phone: str | None = None
    customer_id: int | None = None


class ContactUpdate(BaseModel):
    """Update contact (partial)."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    customer_id: int | None = None


class ContactResponse(BaseModel):
    """Contact response."""

    id: int
    org_id: str
    customer_id: int | None
    name: str
    email: str | None
    phone: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
