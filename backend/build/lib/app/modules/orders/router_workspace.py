"""Workspace orders and briefings: list orders (paid site invoices) and briefings (project requests)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.customer import Customer
from app.models.project_request import ProjectRequest
from app.models.user import User
from app.modules.billing.enums import InvoiceStatus
from app.modules.billing.models import Invoice, Product, Subscription
from app.modules.orders.schemas import BriefingDetail, BriefingItem, OrderItem
from app.modules.projects.models import Project

router = APIRouter(tags=["workspace-orders"])

SITE_DELIVERY_TYPE = "site_delivery"


@router.get("/orders", response_model=list[OrderItem])
async def list_orders(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
):
    """
    List orders: paid invoices for site products (subscription + product provisioning_type=site_delivery).
    Includes project_id and project_status when a project was created after payment.
    """
    # Paid invoices with subscription and product (site_delivery)
    stmt = (
        select(
            Invoice,
            Customer.name.label("customer_name"),
            Product.name.label("product_name"),
            Subscription.id.label("sub_id"),
        )
        .join(Customer, Invoice.customer_id == Customer.id)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Product, Subscription.product_id == Product.id)
        .where(
            Invoice.status == InvoiceStatus.PAID.value,
            func.lower(func.coalesce(Product.provisioning_type, "")) == SITE_DELIVERY_TYPE,
        )
        .order_by(Invoice.paid_at.desc().nullslast(), Invoice.id.desc())
    )
    r = await db.execute(stmt)
    rows = r.all()

    out: list[OrderItem] = []
    for inv, customer_name, product_name, sub_id in rows:
        project_id: int | None = None
        project_status: str | None = None
        proj_r = await db.execute(
            select(Project).where(Project.subscription_id == sub_id).limit(1)
        )
        proj = proj_r.scalar_one_or_none()
        if proj:
            project_id = proj.id
            project_status = proj.status
        status = project_status or "aguardando_briefing"
        out.append(
            OrderItem(
                invoice_id=inv.id,
                customer_id=inv.customer_id,
                customer_name=customer_name or "",
                product_name=product_name or "",
                subscription_id=sub_id,
                project_id=project_id,
                project_status=project_status,
                status=status,
                total=float(inv.total),
                currency=inv.currency or "BRL",
                paid_at=inv.paid_at,
                created_at=inv.created_at,
            )
        )
    return out


@router.get("/briefings", response_model=list[BriefingItem])
async def list_briefings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
    project_id: int | None = Query(None, description="Filter by project_id"),
):
    """List briefings (project requests) with customer name. Optional filter by project_id."""
    stmt = (
        select(ProjectRequest, Customer.name.label("customer_name"))
        .join(Customer, ProjectRequest.customer_id == Customer.id)
        .order_by(ProjectRequest.created_at.desc())
    )
    if project_id is not None:
        stmt = stmt.where(ProjectRequest.project_id == project_id)
    r = await db.execute(stmt)
    rows = r.all()
    return [
        BriefingItem(
            id=pr.id,
            customer_id=pr.customer_id,
            customer_name=customer_name or "",
            project_id=pr.project_id,
            project_name=pr.project_name,
            project_type=pr.project_type,
            description=pr.description,
            status=pr.status,
            created_at=pr.created_at,
        )
        for pr, customer_name in rows
    ]


@router.get("/briefings/{briefing_id}", response_model=BriefingDetail)
async def get_briefing(
    briefing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
) -> BriefingDetail:
    """Get full briefing (project request) by id, including meta."""
    r = await db.execute(
        select(ProjectRequest, Customer.name.label("customer_name")).join(
            Customer, ProjectRequest.customer_id == Customer.id
        ).where(ProjectRequest.id == briefing_id).limit(1)
    )
    row = r.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Briefing not found")
    pr, customer_name = row
    return BriefingDetail(
        id=pr.id,
        customer_id=pr.customer_id,
        customer_name=customer_name or "",
        project_id=pr.project_id,
        project_name=pr.project_name,
        project_type=pr.project_type,
        description=pr.description,
        status=pr.status,
        created_at=pr.created_at,
        meta=pr.meta,
        budget=pr.budget,
        timeline=pr.timeline,
    )


def _format_briefing_as_text(pr: ProjectRequest, customer_name: str) -> str:
    """Format briefing as plain text for download."""
    lines = [
        f"Briefing #{pr.id}",
        f"Cliente: {customer_name}",
        f"Projeto: {pr.project_name}",
        f"Tipo: {pr.project_type}",
        f"Status: {pr.status}",
        f"Data: {pr.created_at.isoformat() if pr.created_at else ''}",
        "",
        "--- Descrição ---",
        pr.description or "(vazio)",
        "",
    ]
    if pr.budget:
        lines.append(f"Orçamento: {pr.budget}\n")
    if pr.timeline:
        lines.append(f"Prazo: {pr.timeline}\n")
    if pr.meta and isinstance(pr.meta, dict):
        lines.append("--- Dados do briefing (meta) ---")
        for k, v in pr.meta.items():
            if v is not None and v != "":
                lines.append(f"{k}: {v}")
    return "\n".join(lines)


@router.get("/briefings/{briefing_id}/download")
async def download_briefing(
    briefing_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
) -> Response:
    """Download briefing as plain text file."""
    r = await db.execute(
        select(ProjectRequest, Customer.name.label("customer_name")).join(
            Customer, ProjectRequest.customer_id == Customer.id
        ).where(ProjectRequest.id == briefing_id).limit(1)
    )
    row = r.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Briefing not found")
    pr, customer_name = row
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in (pr.project_name or "briefing"))
    filename = f"briefing-{pr.id}-{safe_name}.txt"
    content = _format_briefing_as_text(pr, customer_name or "")
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
