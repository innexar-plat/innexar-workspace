"""Billing service: invoices, payment attempts, webhooks."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.encryption import decrypt_value
from app.models.customer import Customer
from app.models.integration_config import IntegrationConfig
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus
from app.modules.billing.models import (
    Invoice,
    MPSubscriptionCheckout,
    PaymentAttempt,
    PricePlan,
    Product,
    Subscription,
    WebhookEvent,
)
from app.providers.payments.base import PaymentLinkResult
from app.providers.payments.mercadopago import MercadoPagoProvider
from app.providers.payments.stripe import StripeProvider

from app.modules.billing.overdue import reactivate_subscription_after_payment

INTERVAL_ONE_TIME = "one_time"


async def _set_subscription_next_due_if_recurring(
    db: AsyncSession, sub: Subscription
) -> None:
    """Set sub.next_due_date to start_date + 30 days only when price plan is recurring (not one_time)."""
    if sub.next_due_date or not sub.start_date:
        return
    pp_r = await db.execute(
        select(PricePlan).where(PricePlan.id == sub.price_plan_id).limit(1)
    )
    pp = pp_r.scalar_one_or_none()
    if pp and (pp.interval or "").lower() != INTERVAL_ONE_TIME:
        sub.next_due_date = sub.start_date + timedelta(days=30)


async def _get_payment_provider(
    db: AsyncSession,
    customer_id: int,
    org_id: str,
    currency: str,
    mode: str = "test",
) -> StripeProvider | MercadoPagoProvider:
    """Resolve provider from IntegrationConfig (customer → tenant → global), else env fallback."""
    provider_name = "mercadopago" if (currency or "BRL").upper() == "BRL" else "stripe"
    key_name = "access_token" if provider_name == "mercadopago" else "secret_key"

    # Mercado Pago: prefer env when set, so .env (MP_ACCESS_TOKEN) overrides DB and avoids 401 from wrong token in IntegrationConfig
    if provider_name == "mercadopago":
        env_token = (os.environ.get("MP_ACCESS_TOKEN") or os.environ.get("MERCADOPAGO_ACCESS_TOKEN") or "").strip()
        if env_token:
            return MercadoPagoProvider(access_token=env_token)

    base_filters = [
        IntegrationConfig.provider == provider_name,
        IntegrationConfig.key == key_name,
        IntegrationConfig.enabled.is_(True),
        IntegrationConfig.mode == mode,
    ]

    # Lookup order: customer → tenant → global
    for scope in ["customer", "tenant", "global"]:
        if scope == "customer":
            q = select(IntegrationConfig).where(
                IntegrationConfig.scope == "customer",
                IntegrationConfig.customer_id == customer_id,
                *base_filters,
            ).limit(1)
        elif scope == "tenant":
            q = select(IntegrationConfig).where(
                IntegrationConfig.scope == "tenant",
                IntegrationConfig.org_id == org_id,
                IntegrationConfig.customer_id.is_(None),
                *base_filters,
            ).limit(1)
        else:
            q = select(IntegrationConfig).where(
                IntegrationConfig.scope == "global",
                IntegrationConfig.customer_id.is_(None),
                *base_filters,
            ).limit(1)
        r = await db.execute(q)
        cfg = r.scalar_one_or_none()
        if cfg and cfg.value_encrypted:
            secret = decrypt_value(cfg.value_encrypted)
            if secret:
                if provider_name == "stripe":
                    return StripeProvider(api_key=secret)
                return MercadoPagoProvider(access_token=secret)

    # Fallback: .env (MP_ACCESS_TOKEN / MERCADOPAGO_ACCESS_TOKEN)
    if provider_name == "stripe":
        return StripeProvider()
    return MercadoPagoProvider()


async def create_manual_invoice(
    db: AsyncSession,
    customer_id: int,
    due_date: datetime,
    total: float,
    currency: str = "BRL",
    line_items: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> Invoice:
    """Create a one-off invoice (no subscription)."""
    inv = Invoice(
        customer_id=customer_id,
        subscription_id=None,
        status=InvoiceStatus.DRAFT.value,
        due_date=due_date,
        total=total,
        currency=currency,
        line_items=line_items if isinstance(line_items, (dict, list)) else None,
    )
    db.add(inv)
    await db.flush()
    return inv


async def create_payment_attempt(
    db: AsyncSession,
    invoice_id: int,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
) -> PaymentLinkResult:
    """Create payment attempt and return payment_url. Raises if invoice not payable."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise ValueError("Invoice not found")
    if inv.status == InvoiceStatus.PAID.value:
        raise ValueError("Invoice already paid")
    currency = (inv.currency or "BRL").upper()
    cust = (await db.execute(select(Customer).where(Customer.id == inv.customer_id).limit(1))).scalar_one_or_none()
    org_id = cust.org_id if cust else "innexar"
    provider = await _get_payment_provider(db, inv.customer_id, org_id, currency)
    description = f"Fatura #{inv.id}" if (inv.currency or "BRL").upper() == "BRL" else f"Invoice #{inv.id}"
    if isinstance(provider, MercadoPagoProvider):
        res = provider.create_payment_link(
            invoice_id=inv.id,
            amount=float(inv.total),
            currency=inv.currency or "BRL",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email,
            customer_name=customer_name,
            customer_phone=customer_phone,
            description=description,
        )
    else:
        res = provider.create_payment_link(
            invoice_id=inv.id,
            amount=float(inv.total),
            currency=inv.currency or "BRL",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email,
            customer_name=customer_name,
            customer_phone=customer_phone,
            description=description,
        )
    attempt = PaymentAttempt(
        invoice_id=invoice_id,
        provider="stripe" if isinstance(provider, StripeProvider) else "mercadopago",
        external_id=res.external_id,
        payment_url=res.payment_url,
        status="pending",
    )
    db.add(attempt)
    inv.status = InvoiceStatus.PENDING.value
    inv.external_id = res.external_id
    await db.flush()
    return res


async def create_subscription_checkout(
    db: AsyncSession,
    invoice_id: int,
    back_url: str,
) -> PaymentLinkResult:
    """Create MP preapproval_plan for Assinaturas, link invoice to plan, return init_point. Raises if invoice not found or already paid."""
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise ValueError("Invoice not found")
    if inv.status == InvoiceStatus.PAID.value:
        raise ValueError("Invoice already paid")
    if not inv.subscription_id:
        raise ValueError("Invoice has no subscription")
    currency = (inv.currency or "BRL").upper()
    cust = (await db.execute(select(Customer).where(Customer.id == inv.customer_id).limit(1))).scalar_one_or_none()
    org_id = cust.org_id if cust else "innexar"
    provider = await _get_payment_provider(db, inv.customer_id, org_id, currency)
    if not isinstance(provider, MercadoPagoProvider):
        raise ValueError("Subscription checkout requires Mercado Pago")
    sub_r = await db.execute(
        select(Subscription, PricePlan, Product)
        .join(PricePlan, Subscription.price_plan_id == PricePlan.id)
        .join(Product, Subscription.product_id == Product.id)
        .where(Subscription.id == inv.subscription_id)
        .limit(1)
    )
    row = sub_r.one_or_none()
    if not row:
        raise ValueError("Subscription or plan not found")
    sub, price_plan, product = row
    reason = product.name or f"Invoice #{invoice_id}"
    if inv.line_items and isinstance(inv.line_items, list) and inv.line_items:
        first = inv.line_items[0]
        if isinstance(first, dict) and first.get("description"):
            reason = str(first["description"])[:255]
    interval = (price_plan.interval or "monthly").lower()
    if interval == "yearly":
        frequency, frequency_type = 12, "months"
    else:
        frequency, frequency_type = 1, "months"
    plan_result = provider.create_subscription_plan(
        reason=reason,
        amount=float(inv.total),
        currency=currency,
        back_url=back_url,
        frequency=frequency,
        frequency_type=frequency_type,
    )
    link = MPSubscriptionCheckout(invoice_id=invoice_id, mp_plan_id=plan_result.plan_id)
    db.add(link)
    inv.status = InvoiceStatus.PENDING.value
    await db.flush()
    return PaymentLinkResult(payment_url=plan_result.init_point, external_id=plan_result.plan_id)


async def mark_invoice_paid(
    db: AsyncSession,
    invoice_id: int,
    *,
    actor_type: str = "staff",
    actor_id: str = "",
    org_id: str = "innexar",
) -> int | None:
    """Mark invoice as paid (manual override). Activates subscription and triggers reactivation. Returns invoice_id if paid (caller may run provisioning in background), None if not found or already paid."""
    inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_id).limit(1))
    inv = inv_result.scalar_one_or_none()
    if not inv:
        return None
    if inv.status == InvoiceStatus.PAID.value:
        return None
    inv.status = InvoiceStatus.PAID.value
    inv.paid_at = datetime.now(timezone.utc)
    if inv.subscription_id:
        sub_r = await db.execute(
            select(Subscription).where(Subscription.id == inv.subscription_id).limit(1)
        )
        sub = sub_r.scalar_one_or_none()
        if sub:
            sub.status = SubscriptionStatus.ACTIVE.value
            if not sub.start_date:
                sub.start_date = datetime.now(timezone.utc)
            await _set_subscription_next_due_if_recurring(db, sub)
            await reactivate_subscription_after_payment(db, sub.id, org_id=org_id)
    await log_audit(
        db,
        entity="invoice",
        entity_id=str(inv.id),
        action="manual_payment_confirmed",
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"org_id": org_id},
    )
    await db.flush()
    return inv.id


async def process_webhook(
    db: AsyncSession,
    provider: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[bool, str, int | None]:
    """Process webhook; idempotent via WebhookEvent. Returns (ok, message, paid_invoice_id or None)."""
    event_id = ""
    if provider == "stripe":
        p = StripeProvider()
        result = p.handle_webhook(body, headers)
        if not result.processed:
            return False, result.message, None
        event_id = result.message
        # Check idempotency
        existing = await db.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == "stripe",
                WebhookEvent.event_id == event_id,
            )
        )
        if existing.scalar_one_or_none():
            return True, "already_processed", None
        payload_hash = hashlib.sha256(body).hexdigest()
        ev = WebhookEvent(provider="stripe", event_id=event_id, payload_hash=payload_hash)
        db.add(ev)
        paid_invoice_id: int | None = None
        if result.invoice_id:
            inv_result = await db.execute(select(Invoice).where(Invoice.id == result.invoice_id))
            inv = inv_result.scalar_one_or_none()
            if inv:
                inv.status = InvoiceStatus.PAID.value
                inv.paid_at = datetime.now(timezone.utc)
                paid_invoice_id = inv.id
                if inv.subscription_id:
                    sub_r = await db.execute(
                        select(Subscription).where(Subscription.id == inv.subscription_id).limit(1)
                    )
                    sub = sub_r.scalar_one_or_none()
                    if sub:
                        sub.status = SubscriptionStatus.ACTIVE.value
                        sub.start_date = datetime.now(timezone.utc)
                        await _set_subscription_next_due_if_recurring(db, sub)
                        await reactivate_subscription_after_payment(db, sub.id, org_id="innexar")
                await log_audit(
                    db,
                    entity="invoice",
                    entity_id=str(inv.id),
                    action="paid",
                    actor_type="webhook",
                    actor_id=event_id,
                    payload={"provider": "stripe"},
                )
        await db.flush()
        return True, "ok", paid_invoice_id

    if provider == "mercadopago":
        p = MercadoPagoProvider()
        result = p.handle_webhook(body, headers)
        if not result.processed:
            return False, result.message, None
        event_id = result.message
        existing = await db.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == "mercadopago",
                WebhookEvent.event_id == event_id,
            )
        )
        if existing.scalar_one_or_none():
            return True, "already_processed", None
        payload_hash = hashlib.sha256(body).hexdigest()
        ev = WebhookEvent(provider="mercadopago", event_id=event_id, payload_hash=payload_hash)
        db.add(ev)
        paid_invoice_id = None
        if result.mp_plan_id and result.mp_preapproval_id:
            link_r = await db.execute(
                select(MPSubscriptionCheckout).where(
                    MPSubscriptionCheckout.mp_plan_id == result.mp_plan_id
                ).limit(1)
            )
            link = link_r.scalar_one_or_none()
            if link:
                inv_result = await db.execute(select(Invoice).where(Invoice.id == link.invoice_id))
                inv = inv_result.scalar_one_or_none()
                if inv and inv.status != InvoiceStatus.PAID.value:
                    inv.status = InvoiceStatus.PAID.value
                    inv.paid_at = datetime.now(timezone.utc)
                    paid_invoice_id = inv.id
                    if inv.subscription_id:
                        sub_r = await db.execute(
                            select(Subscription).where(Subscription.id == inv.subscription_id).limit(1)
                        )
                        sub = sub_r.scalar_one_or_none()
                        if sub:
                            sub.status = SubscriptionStatus.ACTIVE.value
                            sub.external_id = result.mp_preapproval_id
                            sub.start_date = sub.start_date or datetime.now(timezone.utc)
                            await _set_subscription_next_due_if_recurring(db, sub)
                            await reactivate_subscription_after_payment(db, sub.id, org_id="innexar")
                    await log_audit(
                        db,
                        entity="invoice",
                        entity_id=str(inv.id),
                        action="paid",
                        actor_type="webhook",
                        actor_id=event_id,
                        payload={"provider": "mercadopago", "subscription": True},
                    )
        elif result.invoice_id:
            inv_result = await db.execute(select(Invoice).where(Invoice.id == result.invoice_id))
            inv = inv_result.scalar_one_or_none()
            if inv:
                inv.status = InvoiceStatus.PAID.value
                inv.paid_at = datetime.now(timezone.utc)
                paid_invoice_id = inv.id
                if inv.subscription_id:
                    sub_r = await db.execute(
                        select(Subscription).where(Subscription.id == inv.subscription_id).limit(1)
                    )
                    sub = sub_r.scalar_one_or_none()
                    if sub:
                        sub.status = SubscriptionStatus.ACTIVE.value
                        sub.start_date = datetime.now(timezone.utc)
                        await _set_subscription_next_due_if_recurring(db, sub)
                        await reactivate_subscription_after_payment(db, sub.id, org_id="innexar")
                await log_audit(
                    db,
                    entity="invoice",
                    entity_id=str(inv.id),
                    action="paid",
                    actor_type="webhook",
                    actor_id=event_id,
                    payload={"provider": "mercadopago"},
                )
        await db.flush()
        return True, "ok", paid_invoice_id

    return False, "unknown provider", None


async def generate_recurring_invoices(
    db: AsyncSession,
    *,
    org_id: str = "innexar",
    now: datetime | None = None,
    days_before_due: int = 0,
) -> int:
    """For active subscriptions with next_due_date <= (now + days_before_due), create the next invoice and advance next_due_date by 30 days. Returns count of invoices created.
    Use days_before_due=2 to generate invoices 2 days before due so reminders can be sent."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_before_due) if days_before_due else now
    r = await db.execute(
        select(Subscription, PricePlan, Product)
        .join(PricePlan, Subscription.price_plan_id == PricePlan.id)
        .join(Product, Subscription.product_id == Product.id)
        .where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.next_due_date.isnot(None),
            Subscription.next_due_date <= cutoff,
        )
    )
    rows = r.all()
    count = 0
    for sub, price_plan, _product in rows:
        due = sub.next_due_date
        if not due:
            continue
        inv = Invoice(
            customer_id=sub.customer_id,
            subscription_id=sub.id,
            status=InvoiceStatus.PENDING.value,
            due_date=due,
            total=float(price_plan.amount),
            currency=price_plan.currency or "BRL",
            line_items=[
                {
                    "description": f"{_product.name} - {price_plan.name} (recorrente)",
                    "amount": float(price_plan.amount),
                }
            ],
        )
        db.add(inv)
        await db.flush()
        sub.next_due_date = due + timedelta(days=30)
        count += 1
    await db.flush()
    return count


async def charge_recurring_invoices(
    db: AsyncSession,
    *,
    org_id: str = "innexar",
) -> tuple[int, int]:
    """Attempt to charge pending invoices using saved MP cards. Returns (charged, failed)."""
    from app.models.customer import Customer

    # Find pending invoices that have a subscription (recurring)
    r = await db.execute(
        select(Invoice, Subscription, Customer)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Customer, Invoice.customer_id == Customer.id)
        .where(
            Invoice.status == InvoiceStatus.PENDING.value,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Customer.mp_customer_id.isnot(None),
        )
    )
    rows = r.all()
    charged = 0
    failed = 0
    for inv, sub, customer in rows:
        if not customer.mp_customer_id:
            continue
        try:
            provider = await _get_payment_provider(db, customer.id, org_id, inv.currency or "BRL")
            if not isinstance(provider, MercadoPagoProvider):
                continue
            # Get customer's saved cards
            import httpx
            with httpx.Client(timeout=10.0) as client:
                cards_resp = client.get(
                    f"https://api.mercadopago.com/v1/customers/{customer.mp_customer_id}/cards",
                    headers={"Authorization": f"Bearer {provider._access_token}"},
                )
            if cards_resp.status_code != 200 or not cards_resp.json():
                continue
            cards = cards_resp.json()
            card_id = str(cards[0].get("id", ""))
            if not card_id:
                continue
            description_parts = []
            if inv.line_items and isinstance(inv.line_items, list):
                first = inv.line_items[0]
                if isinstance(first, dict):
                    description_parts.append(str(first.get("description", "")))
            description = description_parts[0] if description_parts else f"Invoice #{inv.id}"

            payment = provider.charge_saved_card(
                customer_id=customer.mp_customer_id,
                card_id=card_id,
                amount=float(inv.total),
                description=description,
                external_reference=str(inv.id),
            )
            pay_status = (payment.get("status") or "").lower()
            if pay_status == "approved":
                inv.status = InvoiceStatus.PAID.value
                inv.paid_at = datetime.now(timezone.utc)
                inv.external_id = str(payment.get("id", ""))
                await reactivate_subscription_after_payment(db, sub.id, org_id=org_id)
                charged += 1
            else:
                failed += 1
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to charge recurring invoice %s for customer %s",
                inv.id, customer.id, exc_info=True,
            )
            failed += 1
    await db.flush()
    return charged, failed


async def send_invoice_reminders(
    db: AsyncSession,
    background_tasks: "BackgroundTasks",
    *,
    org_id: str = "innexar",
    days_ahead: int = 2,
    now: datetime | None = None,
) -> int:
    """Find PENDING invoices with due_date within the next days_ahead days and reminder_sent_at null;
    create in-app notification + send email for each customer user and set reminder_sent_at. Returns count of invoices reminded."""
    from sqlalchemy.orm import selectinload

    from app.modules.notifications.service import create_notification_and_maybe_send_email

    if now is None:
        now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    r = await db.execute(
        select(Invoice)
        .where(
            Invoice.status == InvoiceStatus.PENDING.value,
            Invoice.due_date >= now,
            Invoice.due_date <= end,
            Invoice.reminder_sent_at.is_(None),
        )
    )
    invoices = r.scalars().all()
    reminded = 0
    for inv in invoices:
        # Load customer with users
        cu_r = await db.execute(
            select(Customer).where(Customer.id == inv.customer_id).options(
                selectinload(Customer.users)
            )
        )
        customer = cu_r.scalar_one_or_none()
        if not customer or not customer.users:
            inv.reminder_sent_at = now
            reminded += 1
            continue
        due_str = inv.due_date.strftime("%d/%m/%Y") if inv.due_date else ""
        total_str = f"R$ {inv.total:.2f}" if (inv.currency or "").upper() == "BRL" else f"{inv.total:.2f} {inv.currency or ''}"
        title = "Lembrete: fatura em breve"
        body = f"Sua fatura #{inv.id} vence em {due_str}. Valor: {total_str}. Acesse o portal para pagar."
        for cu in customer.users:
            await create_notification_and_maybe_send_email(
                db,
                background_tasks,
                customer_user_id=cu.id,
                channel="in_app,email",
                title=title,
                body=body,
                recipient_email=cu.email,
                org_id=org_id,
            )
        inv.reminder_sent_at = now
        reminded += 1
    await db.flush()
    return reminded

