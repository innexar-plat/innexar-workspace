"""Workspace support routes: tickets."""
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.models.user import User
from app.modules.notifications.service import create_notification_and_maybe_send_email
from app.modules.support.models import Ticket, TicketMessage
from app.modules.support.schemas import (
    TicketCreate,
    TicketMessageCreate,
    TicketMessageResponse,
    TicketResponse,
)

router = APIRouter(prefix="/support", tags=["workspace-support"])


@router.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("support:read"))],
    category: str | None = Query(None, description="Filter by category"),
    project_id: int | None = Query(None, description="Filter by project_id"),
):
    """List tickets (workspace). Optional filters: category, project_id."""
    q = select(Ticket).order_by(Ticket.id.desc())
    if category is not None:
        q = q.where(Ticket.category == category)
    if project_id is not None:
        q = q.where(Ticket.project_id == project_id)
    r = await db.execute(q)
    return list(r.scalars().all())


@router.post("/tickets", response_model=TicketResponse, status_code=201)
async def create_ticket(
    body: TicketCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
    current: Annotated[User, Depends(RequirePermission("support:write"))],
):
    """Create ticket (workspace)."""
    if body.customer_id is None:
        raise HTTPException(status_code=400, detail="customer_id required")
    category = (body.category or "suporte").strip() or "suporte"
    t = Ticket(
        customer_id=body.customer_id,
        subject=body.subject,
        status="open",
        category=category,
        project_id=body.project_id,
    )
    db.add(t)
    await db.flush()
    await db.refresh(t)
    cust = (await db.execute(select(Customer).where(Customer.id == body.customer_id).limit(1))).scalar_one_or_none()
    cu_r = await db.execute(select(CustomerUser).where(CustomerUser.customer_id == body.customer_id).limit(1))
    cu = cu_r.scalar_one_or_none()
    recipient = (cust.email if cust else None) or (cu.email if cu else None)
    if recipient:
        await create_notification_and_maybe_send_email(
            db,
            background_tasks,
            customer_user_id=cu.id if cu else None,
            channel="in_app,email",
            title="New ticket",
            body=f"Ticket: {t.subject}",
            recipient_email=recipient,
            org_id=current.org_id or "innexar",
        )
    return t


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("support:read"))],
):
    """Get ticket by id."""
    r = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=201)
async def add_ticket_message(
    ticket_id: int,
    body: TicketMessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("support:write"))],
):
    """Add message to ticket (as staff)."""
    r = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    t = r.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    msg = TicketMessage(
        ticket_id=ticket_id,
        author_type="staff",
        body=body.body,
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    return msg