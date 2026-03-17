"""Support schemas."""
from datetime import datetime

from pydantic import BaseModel


class TicketCreate(BaseModel):
    """Create ticket (workspace: customer_id required; portal: uses current customer)."""

    subject: str
    customer_id: int | None = None
    category: str | None = None  # suporte_tecnico, alteracao_site, modificacao, novo_projeto, financeiro
    project_id: int | None = None


class TicketMessageCreate(BaseModel):
    """Create ticket message."""

    body: str


class TicketMessageResponse(BaseModel):
    """Ticket message response."""

    id: int
    ticket_id: int
    author_type: str
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketResponse(BaseModel):
    """Ticket response."""

    id: int
    org_id: str
    customer_id: int
    subject: str
    status: str
    category: str
    project_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TicketUpdate(BaseModel):
    """Update ticket (partial)."""

    status: str | None = None
    category: str | None = None
    project_id: int | None = None
