"""Overdue: suspend Hestia user and subscription when invoice not paid after grace period."""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hestia_settings import HestiaSettings
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus
from app.modules.billing.models import Invoice, Product, ProvisioningRecord, Subscription
from app.providers.hestia.loader import get_hestia_client

logger = logging.getLogger(__name__)


async def process_overdue_invoices(db: AsyncSession, org_id: str = "innexar") -> int:
    """Find pending invoices past due_date + grace_period, suspend Hestia user and set subscription SUSPENDED. Returns count processed."""
    settings_r = await db.execute(select(HestiaSettings).where(HestiaSettings.org_id == org_id).limit(1))
    hestia_settings = settings_r.scalar_one_or_none()
    if not hestia_settings or not hestia_settings.auto_suspend_enabled:
        return 0
    grace_days = hestia_settings.grace_period_days
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)
    r = await db.execute(
        select(Invoice, Subscription, Product)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Product, Subscription.product_id == Product.id)
        .where(
            Invoice.status == InvoiceStatus.PENDING.value,
            Invoice.due_date < cutoff,
            Subscription.status.in_([SubscriptionStatus.ACTIVE.value, SubscriptionStatus.OVERDUE.value]),
            (Product.provisioning_type or "").lower() == "hestia_hosting",
        )
    )
    rows = r.all()
    client = await get_hestia_client(db, org_id=org_id)
    count = 0
    for inv, sub, _product in rows:
        sub.status = SubscriptionStatus.SUSPENDED.value
        rec_r = await db.execute(
            select(ProvisioningRecord).where(
                ProvisioningRecord.subscription_id == sub.id,
                ProvisioningRecord.provider == "hestia",
                ProvisioningRecord.status == "provisioned",
            ).limit(1)
        )
        rec = rec_r.scalar_one_or_none()
        if rec and client:
            try:
                client.suspend_user(rec.external_user)
                logger.info("Suspended Hestia user %s for overdue invoice %s", rec.external_user, inv.id)
            except Exception as e:
                logger.warning("Failed to suspend Hestia user %s: %s", rec.external_user, e)
        count += 1
    await db.flush()
    return count


async def reactivate_subscription_after_payment(db: AsyncSession, subscription_id: int, org_id: str = "innexar") -> None:
    """If subscription was SUSPENDED, unsuspend Hestia user."""
    sub_r = await db.execute(select(Subscription).where(Subscription.id == subscription_id).limit(1))
    sub = sub_r.scalar_one_or_none()
    if not sub or sub.status != SubscriptionStatus.SUSPENDED.value:
        return
    rec_r = await db.execute(
        select(ProvisioningRecord).where(
            ProvisioningRecord.subscription_id == subscription_id,
            ProvisioningRecord.provider == "hestia",
            ProvisioningRecord.status == "provisioned",
        ).limit(1)
    )
    rec = rec_r.scalar_one_or_none()
    if not rec:
        return
    client = await get_hestia_client(db, org_id=org_id)
    if not client:
        return
    try:
        client.unsuspend_user(rec.external_user)
        logger.info("Unsuspended Hestia user %s for subscription %s", rec.external_user, subscription_id)
    except Exception as e:
        logger.warning("Failed to unsuspend Hestia user %s: %s", rec.external_user, e)


async def sync_subscription_status_to_hestia(
    db: AsyncSession, subscription_id: int, new_status: str, org_id: str = "innexar"
) -> None:
    """When subscription status is changed manually (PATCH), sync with Hestia: suspend or unsuspend user."""
    rec_r = await db.execute(
        select(ProvisioningRecord).where(
            ProvisioningRecord.subscription_id == subscription_id,
            ProvisioningRecord.provider == "hestia",
            ProvisioningRecord.status == "provisioned",
        ).limit(1)
    )
    rec = rec_r.scalar_one_or_none()
    if not rec:
        return
    client = await get_hestia_client(db, org_id=org_id)
    if not client:
        return
    if new_status in (SubscriptionStatus.SUSPENDED.value, SubscriptionStatus.CANCELED.value):
        try:
            client.suspend_user(rec.external_user)
            logger.info(
                "Suspended Hestia user %s for subscription %s (manual status=%s)",
                rec.external_user,
                subscription_id,
                new_status,
            )
        except Exception as e:
            logger.warning("Failed to suspend Hestia user %s: %s", rec.external_user, e)
    elif new_status == SubscriptionStatus.ACTIVE.value:
        try:
            client.unsuspend_user(rec.external_user)
            logger.info(
                "Unsuspended Hestia user %s for subscription %s (manual status=active)",
                rec.external_user,
                subscription_id,
            )
        except Exception as e:
            logger.warning("Failed to unsuspend Hestia user %s: %s", rec.external_user, e)
