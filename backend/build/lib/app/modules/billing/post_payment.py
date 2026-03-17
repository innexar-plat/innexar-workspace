"""Post-payment actions: create project for site products and notify team."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notification import Notification
from app.models.user import User
from app.modules.billing.models import Invoice, Product, Subscription
from app.modules.projects.models import Project

SITE_DELIVERY_PROVISIONING_TYPE = "site_delivery"
STATUS_AGUARDANDO_BRIEFING = "aguardando_briefing"


def _is_site_product(product: Product) -> bool:
    """True if product is a site delivery (creates a project)."""
    pt = (product.provisioning_type or "").lower()
    if pt == SITE_DELIVERY_PROVISIONING_TYPE:
        return True
    return "site" in (product.name or "").lower()


async def create_project_and_notify_after_payment(db: AsyncSession, invoice_id: int) -> None:
    """
    After payment: if invoice is for a site product, create Project (aguardando_briefing)
    and notify staff. Idempotent: skips if project already exists for this subscription.
    """
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.subscription).selectinload(Subscription.product),
            selectinload(Invoice.customer),
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice or not invoice.subscription_id:
        return

    subscription = invoice.subscription
    if not subscription or not subscription.product:
        return

    product = subscription.product
    if not _is_site_product(product):
        return

    # Idempotent: do not create duplicate project for same subscription
    existing = await db.execute(
        select(Project).where(Project.subscription_id == subscription.id).limit(1)
    )
    if existing.scalar_one_or_none():
        return

    customer = invoice.customer
    project_name = f"Site {customer.name}" if customer else f"Site (pedido #{invoice_id})"

    expected_delivery_at: datetime | None = None
    # Optional: from product metadata or line_items (e.g. delivery_hours)
    line_items = invoice.line_items or {}
    if isinstance(line_items, dict):
        items = line_items.get("items") or line_items.get("line_items") or []
        if items and isinstance(items, list) and len(items) > 0:
            first = items[0] if isinstance(items[0], dict) else None
            if first and isinstance(first.get("delivery_hours"), (int, float)):
                hours = int(first.get("delivery_hours", 48))
                expected_delivery_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    project = Project(
        org_id=invoice.customer.org_id if customer else "innexar",
        customer_id=invoice.customer_id,
        name=project_name,
        status=STATUS_AGUARDANDO_BRIEFING,
        subscription_id=subscription.id,
        expected_delivery_at=expected_delivery_at,
    )
    db.add(project)
    await db.flush()

    # Notify all staff users (in-app)
    org_id = getattr(customer, "org_id", "innexar") if customer else "innexar"
    staff_result = await db.execute(select(User).where(User.org_id == org_id))
    for user in staff_result.scalars().all():
        notification = Notification(
            user_id=user.id,
            customer_user_id=None,
            channel="in_app",
            title="Novo pedido – aguardando briefing",
            body=f"Pedido #{invoice_id} – Cliente: {customer.name if customer else 'N/A'}. "
            f"Projeto #{project.id} criado. Aguardando preenchimento do briefing.",
        )
        db.add(notification)
    await db.flush()
