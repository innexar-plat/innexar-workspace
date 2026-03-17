"""Provisioning: after invoice paid, provision Hestia hosting if product type is hestia_hosting."""
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encrypt_value
from app.models.customer import Customer
from app.models.hestia_settings import HestiaSettings
from app.modules.billing.models import (
    Invoice,
    Product,
    ProvisioningJob,
    ProvisioningRecord,
    Subscription,
)
from app.providers.hestia.loader import get_hestia_client

try:
    from app.providers.cloudflare.loader import get_cloudflare_client
except ImportError:
    get_cloudflare_client = None  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

HOSTING_PROVISIONING_TYPE = "hestia_hosting"


def _append_log(job: ProvisioningJob, message: str) -> None:
    """Append a line to job logs."""
    prefix = f"[{datetime.now(UTC).isoformat()}] "
    current = (job.logs or "") + prefix + message + "\n"
    if len(current) > 32_000:
        current = "... (truncated)\n" + current[-32_000:]
    job.logs = current


def _domain_from_line_items(line_items: Any) -> str | None:
    """Extract domain from invoice line_items (first item with 'domain' key or first list item)."""
    if not line_items:
        return None
    if isinstance(line_items, list):
        for item in line_items:
            if isinstance(item, dict) and item.get("domain"):
                return str(item["domain"]).strip()
    if isinstance(line_items, dict) and line_items.get("domain"):
        return str(line_items["domain"]).strip()
    return None


def _sanitize_hestia_user(customer_id: int, domain: str) -> str:
    """Generate valid Hestia username (alphanumeric + underscore)."""
    safe = re.sub(r"[^a-z0-9]", "", domain.lower().replace(".", "")[:20])
    return f"cust{customer_id}_{safe}"[:32]


async def trigger_provisioning_if_needed(db: AsyncSession, invoice_id: int) -> None:
    """If invoice is for a hestia_hosting product, provision user+domain on Hestia and create ProvisioningRecord."""
    r = await db.execute(
        select(Invoice, Subscription, Product)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Product, Subscription.product_id == Product.id)
        .where(Invoice.id == invoice_id)
    )
    row = r.one_or_none()
    if not row:
        return
    inv, sub, product = row
    if (product.provisioning_type or "").lower() != HOSTING_PROVISIONING_TYPE:
        return

    job = ProvisioningJob(
        subscription_id=sub.id,
        invoice_id=inv.id,
        status="queued",
        step=None,
        attempts=1,
    )
    db.add(job)
    await db.flush()

    domain = _domain_from_line_items(inv.line_items)
    if not domain:
        logger.warning("Provisioning skipped: no domain in invoice %s line_items", invoice_id)
        job.status = "failed"
        job.step = "create_user"
        job.last_error = "no domain in line_items"
        job.completed_at = datetime.now(UTC)
        _append_log(job, "Skipped: no domain in line_items")
        rec = ProvisioningRecord(
            subscription_id=sub.id,
            invoice_id=inv.id,
            provider="hestia",
            external_user="",
            domain="",
            status="failed",
            meta={"error": "no domain in line_items"},
        )
        db.add(rec)
        await db.flush()
        return

    cust_r = await db.execute(select(Customer).where(Customer.id == inv.customer_id).limit(1))
    customer = cust_r.scalar_one_or_none()
    customer_email = customer.email if customer else ""
    org_id = (customer.org_id if customer else None) or "innexar"
    client = await get_hestia_client(db, org_id=org_id)
    if not client:
        logger.warning("Provisioning skipped: no Hestia client for org %s", org_id)
        job.status = "failed"
        job.step = "create_user"
        job.last_error = "Hestia not configured"
        job.completed_at = datetime.now(UTC)
        _append_log(job, "Hestia not configured for org")
        rec = ProvisioningRecord(
            subscription_id=sub.id,
            invoice_id=inv.id,
            provider="hestia",
            external_user="",
            domain=domain,
            status="failed",
            meta={"error": "Hestia not configured"},
        )
        db.add(rec)
        await db.flush()
        return

    hestia_user = _sanitize_hestia_user(inv.customer_id, domain)
    password = secrets.token_urlsafe(16)
    package = product.hestia_package or "default"
    try:
        settings_r = await db.execute(
            select(HestiaSettings).where(HestiaSettings.org_id == org_id).limit(1)
        )
        hestia_settings = settings_r.scalar_one_or_none()
        if hestia_settings and hestia_settings.default_hestia_package:
            package = hestia_settings.default_hestia_package
    except Exception:
        pass

    job.status = "running"
    job.step = "create_user"
    _append_log(job, f"Creating user {hestia_user} package={package}")
    await db.flush()

    try:
        client.create_user(user=hestia_user, password=password, email=customer_email or "", package=package)
        _append_log(job, "User created")
        job.step = "add_domain"
        await db.flush()
        client.ensure_domain(user=hestia_user, domain=domain)
        _append_log(job, f"Domain {domain} ensured")
        job.step = "create_mail"
        await db.flush()
        client.ensure_mail(user=hestia_user, domain=domain, enabled=True)
        _append_log(job, "Mail ensured")

        if get_cloudflare_client:
            cf = await get_cloudflare_client(db, org_id=org_id)
            if cf:
                try:
                    job.step = "create_cloudflare_zone"
                    await db.flush()
                    zone = cf.get_zone_by_name(domain)
                    if not zone:
                        zone = cf.create_zone(domain)
                        ns = zone.get("name_servers") or []
                        _append_log(job, f"Cloudflare zone created; nameservers: {', '.join(ns)}")
                    else:
                        _append_log(job, "Cloudflare zone already exists")
                    zone_id = zone.get("id")
                    if zone_id:
                        job.step = "create_cloudflare_records"
                        await db.flush()
                        cf.create_dns_record(zone_id, "MX", domain, f"mail.{domain}", priority=10)
                        cf.create_dns_record(zone_id, "TXT", domain, "v=spf1 a mx ~all")
                        _append_log(job, "Cloudflare MX and SPF records created")
                except Exception as cf_err:
                    _append_log(job, f"Cloudflare step skipped/error: {cf_err}")

        job.step = "finalize"
        await db.flush()
    except Exception as e:
        logger.exception("Hestia provisioning failed for invoice %s: %s", invoice_id, e)
        job.status = "failed"
        job.last_error = str(e)
        job.completed_at = datetime.now(UTC)
        _append_log(job, f"Error: {e}")
        rec = ProvisioningRecord(
            subscription_id=sub.id,
            invoice_id=inv.id,
            provider="hestia",
            external_user=hestia_user,
            domain=domain,
            status="failed",
            meta={"error": str(e)},
        )
        db.add(rec)
        await db.flush()
        return

    site_url = f"https://{domain}"
    panel_url = getattr(client, "base_url", "") or ""
    panel_password_encrypted = encrypt_value(password) if password else None
    rec = ProvisioningRecord(
        subscription_id=sub.id,
        invoice_id=inv.id,
        provider="hestia",
        external_user=hestia_user,
        domain=domain,
        site_url=site_url,
        panel_login=hestia_user,
        panel_password_encrypted=panel_password_encrypted,
        panel_url=panel_url,
        status="provisioned",
        provisioned_at=datetime.now(UTC),
    )
    db.add(rec)
    job.status = "success"
    job.completed_at = datetime.now(UTC)
    _append_log(job, "Provisioning completed")
    await db.flush()
    logger.info("Provisioned Hestia for invoice %s: user=%s domain=%s", invoice_id, hestia_user, domain)
