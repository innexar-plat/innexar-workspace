"""Workspace dashboard: summary (counts and totals) and revenue series."""
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.user import User
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus
from app.modules.billing.models import Invoice, Subscription
from app.modules.dashboard.schemas import (
    DashboardCustomersSummary,
    DashboardInvoicesSummary,
    DashboardProjectsSummary,
    DashboardRevenuePoint,
    DashboardRevenueResponse,
    DashboardSubscriptionsSummary,
    DashboardSummaryResponse,
    DashboardTicketsSummary,
)
from app.modules.projects.models import Project
from app.modules.support.models import Ticket
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/dashboard", tags=["workspace-dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("dashboard:read"))],
) -> DashboardSummaryResponse:
    """Get dashboard summary: active customers, invoices, subscriptions, tickets, projects (counts and totals)."""
    # Active customers: at least 1 Subscription active or 1 Invoice paid
    sub_ids = select(Subscription.customer_id).where(Subscription.status == SubscriptionStatus.ACTIVE.value).distinct()
    inv_ids = select(Invoice.customer_id).where(Invoice.status == InvoiceStatus.PAID.value).distinct()
    union_ids = sub_ids.union(inv_ids).subquery()
    r = await db.execute(select(func.count()).select_from(union_ids))
    active_customers = r.scalar() or 0

    # Invoices: total, pending, paid, total_paid_amount
    r = await db.execute(select(func.count()).select_from(Invoice))
    inv_total = r.scalar() or 0
    r = await db.execute(select(func.count()).select_from(Invoice).where(Invoice.status == InvoiceStatus.PENDING.value))
    inv_pending = r.scalar() or 0
    r = await db.execute(select(func.count()).select_from(Invoice).where(Invoice.status == InvoiceStatus.PAID.value))
    inv_paid_count = r.scalar() or 0
    r = await db.execute(
        select(func.coalesce(func.sum(Invoice.total), 0)).where(Invoice.status == InvoiceStatus.PAID.value)
    )
    total_paid_amount = float(r.scalar() or 0)

    # Subscriptions: active, canceled, total
    r = await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE.value)
    )
    sub_active_count = r.scalar() or 0
    r = await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.CANCELED.value)
    )
    sub_canceled = r.scalar() or 0
    r = await db.execute(select(func.count()).select_from(Subscription))
    sub_total = r.scalar() or 0

    # Tickets: open, closed
    r = await db.execute(select(func.count()).select_from(Ticket).where(Ticket.status == "open"))
    tickets_open = r.scalar() or 0
    r = await db.execute(select(func.count()).select_from(Ticket).where(Ticket.status == "closed"))
    tickets_closed = r.scalar() or 0

    # Projects: by status, total
    r_proj = await db.execute(select(Project.status, func.count()).select_from(Project).group_by(Project.status))
    by_status: dict[str, int] = {str(row[0]): row[1] for row in r_proj.all()}
    r = await db.execute(select(func.count()).select_from(Project))
    projects_total = r.scalar() or 0

    return DashboardSummaryResponse(
        customers=DashboardCustomersSummary(active=active_customers),
        invoices=DashboardInvoicesSummary(
            total=inv_total,
            pending=inv_pending,
            paid=inv_paid_count,
            total_paid_amount=total_paid_amount,
        ),
        subscriptions=DashboardSubscriptionsSummary(
            active=sub_active_count,
            canceled=sub_canceled,
            total=sub_total,
        ),
        tickets=DashboardTicketsSummary(open=tickets_open, closed=tickets_closed),
        projects=DashboardProjectsSummary(by_status=by_status, total=projects_total),
    )


def _period_key(paid_at: datetime | None, period_type: str) -> str:
    """Return period key for grouping: YYYY-MM-DD, YYYY-Www, or YYYY-MM."""
    if not paid_at:
        return ""
    if period_type == "day":
        return paid_at.strftime("%Y-%m-%d")
    if period_type == "week":
        # ISO week: YYYY-Wnn
        return paid_at.strftime("%Y-W%W")
    if period_type == "month":
        return paid_at.strftime("%Y-%m")
    return paid_at.strftime("%Y-%m-%d")


PeriodType = Literal["day", "week", "month"]


@router.get("/revenue", response_model=DashboardRevenueResponse)
async def get_dashboard_revenue(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("dashboard:read"))],
    period_type: Annotated[
        PeriodType, Query(description="day, week, or month")
    ] = "month",
    start_date: datetime | None = Query(None, description="Start of range (UTC)"),  # noqa: B008
    end_date: datetime | None = Query(None, description="End of range (UTC)"),  # noqa: B008
) -> DashboardRevenueResponse:
    """Get revenue time series (paid invoices) for charts. Filter by period type and date range."""
    end = end_date or datetime.now(UTC)
    start = start_date or (end - timedelta(days=365))
    if start > end:
        start, end = end, start
    r = await db.execute(
        select(Invoice.paid_at, Invoice.total).where(
            Invoice.status == InvoiceStatus.PAID.value,
            Invoice.paid_at.isnot(None),
            Invoice.paid_at >= start,
            Invoice.paid_at <= end,
        )
    )
    rows = r.all()
    by_period: defaultdict[str, float] = defaultdict(float)
    for paid_at, total in rows:
        key = _period_key(paid_at, period_type)
        if key:
            by_period[key] += float(total)
    series = [DashboardRevenuePoint(period=k, revenue=round(v, 2)) for k, v in sorted(by_period.items())]
    return DashboardRevenueResponse(period_type=period_type, series=series)
