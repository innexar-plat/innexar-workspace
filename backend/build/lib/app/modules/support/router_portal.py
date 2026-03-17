"""Portal support routes: tickets for current customer."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_customer import get_current_customer
from app.core.database import get_db
from app.core.feature_flags import require_portal_feature
from app.models.customer_user import CustomerUser
from app.modules.support.models import Ticket, TicketMessage
from app.modules.support.schemas import (
    TicketCreate,
    TicketMessageCreate,
    TicketMessageResponse,
    TicketResponse,
)

router = APIRouter(prefix="/tickets", tags=["portal-support"])


@router.get("", response_model=list[TicketResponse])
async def list_my_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.tickets.enabled")],
):
    """List tickets for the current customer."""
    r = await db.execute(
        select(Ticket)
        .where(Ticket.customer_id == current.customer_id)
        .order_by(Ticket.id.desc())
    )
    return list(r.scalars().all())


@router.post("", response_model=TicketResponse, status_code=201)
async def create_ticket(
    body: TicketCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.tickets.enabled")],
):
    """Create ticket (as current customer). Optionally link to project (must own project)."""
    project_id = body.project_id
    if project_id is not None:
        from app.modules.projects.models import Project
        r = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.customer_id == current.customer_id,
            )
        )
        if not r.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Project not found or not yours")
    category = (body.category or "suporte").strip() or "suporte"
    t = Ticket(
        customer_id=current.customer_id,
        subject=body.subject,
        status="open",
        category=category,
        project_id=project_id,
    )
    db.add(t)
    await db.flush()
    await db.refresh(t)
    return t


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_my_ticket(
    ticket_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.tickets.enabled")],
):
    """Get ticket (only if owned by current customer)."""
    r = await db.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.customer_id == current.customer_id,
        )
    )
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("/{ticket_id}/messages", response_model=list[TicketMessageResponse])
async def list_my_ticket_messages(
    ticket_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.tickets.enabled")],
):
    """List messages for a ticket (only if owned by current customer)."""
    r = await db.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.customer_id == current.customer_id,
        )
    )
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    r2 = await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.id)
    )
    return list(r2.scalars().all())


@router.post(
    "/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=201
)
async def add_ticket_message(
    ticket_id: int,
    body: TicketMessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.tickets.enabled")],
):
    """Add message to ticket (as customer)."""
    r = await db.execute(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.customer_id == current.customer_id,
        )
    )
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    msg = TicketMessage(
        ticket_id=ticket_id,
        author_type="customer",
        body=body.body,
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    return msg
