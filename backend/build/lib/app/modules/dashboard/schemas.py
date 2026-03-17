"""Dashboard workspace schemas."""
from pydantic import BaseModel


class DashboardInvoicesSummary(BaseModel):
    """Invoices: total, pending, paid, total_paid_amount."""

    total: int
    pending: int
    paid: int
    total_paid_amount: float


class DashboardSubscriptionsSummary(BaseModel):
    """Subscriptions: active, canceled, total."""

    active: int
    canceled: int
    total: int


class DashboardTicketsSummary(BaseModel):
    """Tickets: open, closed."""

    open: int
    closed: int


class DashboardProjectsSummary(BaseModel):
    """Projects by status (e.g. active, delivered)."""

    by_status: dict[str, int]
    total: int


class DashboardCustomersSummary(BaseModel):
    """Active customers (with active subscription or paid invoice)."""

    active: int


class DashboardSummaryResponse(BaseModel):
    """Dashboard summary: counts and totals (RBAC: dashboard:read)."""

    customers: DashboardCustomersSummary | None = None
    invoices: DashboardInvoicesSummary | None = None
    subscriptions: DashboardSubscriptionsSummary | None = None
    tickets: DashboardTicketsSummary | None = None
    projects: DashboardProjectsSummary | None = None


class DashboardRevenuePoint(BaseModel):
    """Single period revenue."""

    period: str
    revenue: float


class DashboardRevenueResponse(BaseModel):
    """Revenue time series for charts."""

    period_type: str
    series: list[DashboardRevenuePoint]
