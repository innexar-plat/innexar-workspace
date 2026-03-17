"""Public checkout: start checkout (create customer/subscription/invoice, process payment)."""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.core.security import hash_password
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.modules.billing.enums import InvoiceStatus, SubscriptionStatus
from app.modules.billing.models import Invoice, PricePlan, Product, Subscription
from app.modules.billing.post_payment import create_project_and_notify_after_payment
from app.modules.billing.service import _get_payment_provider, create_payment_attempt
from app.modules.billing.overdue import reactivate_subscription_after_payment
from app.modules.checkout.schemas import CheckoutStartRequest, CheckoutStartResponse
from app.modules.customers.service import send_portal_credentials_after_payment
from app.modules.notifications.service import create_notification_and_maybe_send_email
from app.providers.payments.mercadopago import MercadoPagoProvider

router = APIRouter(prefix="/checkout", tags=["public-checkout"])
ORG_ID = "innexar"
logger = logging.getLogger(__name__)


async def _run_create_project_after_payment(invoice_id: int) -> None:
    """Background: create project for site product and notify staff (same as webhook path)."""
    async with AsyncSessionLocal() as db:
        try:
            await create_project_and_notify_after_payment(db, invoice_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@router.post("/start", response_model=CheckoutStartResponse)
async def checkout_start(
    body: CheckoutStartRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
) -> CheckoutStartResponse:
    """Start checkout: find/create Customer, create Subscription+Invoice, process payment via Bricks or Checkout Pro."""
    email = body.customer_email.lower().strip()

    # ── Resolve product and price plan ────────────────────────────────
    pp = (
        await db.execute(
            select(PricePlan).where(
                PricePlan.id == body.price_plan_id,
                PricePlan.product_id == body.product_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if not pp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product or price plan not found",
        )
    product = (await db.execute(select(Product).where(Product.id == body.product_id).limit(1))).scalar_one_or_none()
    if not product or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or inactive",
        )
    provisioning_type = (product.provisioning_type or "").lower()
    if provisioning_type == "hestia_hosting":
        domain = (body.domain or "").strip()
        if not domain:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Domain is required for hosting products",
            )

    # ── Find or create Customer + CustomerUser ────────────────────────
    cu = (await db.execute(select(CustomerUser).where(CustomerUser.email == email).limit(1))).scalar_one_or_none()
    existing_customer = cu is not None
    if cu:
        customer_id = cu.customer_id
        cust = (await db.execute(select(Customer).where(Customer.id == customer_id).limit(1))).scalar_one_or_none()
    else:
        cust = (await db.execute(select(Customer).where(Customer.email == email).limit(1))).scalar_one_or_none()
        if cust:
            customer_id = cust.id
            cu_new = CustomerUser(
                customer_id=customer_id,
                email=email,
                password_hash=hash_password(secrets.token_urlsafe(16)),
                email_verified=False,
            )
            db.add(cu_new)
            await db.flush()
        else:
            cust = Customer(org_id=ORG_ID, name=body.customer_name or email, email=email, phone=body.customer_phone)
            db.add(cust)
            await db.flush()
            customer_id = cust.id
            cu_new = CustomerUser(
                customer_id=customer_id,
                email=email,
                password_hash=hash_password(secrets.token_urlsafe(16)),
                email_verified=False,
            )
            db.add(cu_new)
            await db.flush()

    # ── Create Subscription (inactive) + Invoice ──────────────────────
    sub = Subscription(
        customer_id=customer_id,
        product_id=body.product_id,
        price_plan_id=body.price_plan_id,
        status=SubscriptionStatus.INACTIVE.value,
    )
    db.add(sub)
    await db.flush()

    due = datetime.now(timezone.utc) + timedelta(days=7)
    line_items: list[dict] = [
        {"description": f"{product.name} - {pp.name}", "amount": float(pp.amount)}
    ]
    if body.domain and provisioning_type == "hestia_hosting":
        line_items[0]["domain"] = body.domain.strip()
    if body.fidelity_12_months_accepted is True:
        line_items[0]["fidelity_12_months_accepted"] = True
        line_items[0]["fidelity_accepted_at"] = datetime.now(timezone.utc).isoformat()

    inv = Invoice(
        customer_id=customer_id,
        subscription_id=sub.id,
        status=InvoiceStatus.DRAFT.value,
        due_date=due,
        total=float(pp.amount),
        currency=pp.currency or "BRL",
        line_items=line_items,
    )
    db.add(inv)
    await db.flush()

    # ── Bricks flow: process payment directly if payment_method_id is present ─────────
    if body.payment_method_id:
        return await _process_bricks_payment(
            db, background_tasks, body, inv, sub, pp, cust, email, existing_customer
        )

    # ── Legacy flow: redirect to Checkout Pro ─────────────────────────
    try:
        res = await create_payment_attempt(
            db,
            invoice_id=inv.id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            customer_email=email,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return CheckoutStartResponse(payment_url=res.payment_url, existing_customer=existing_customer)


def _is_recurring_interval(interval: str | None) -> bool:
    """True if plan generates recurring invoices (month/monthly/year/yearly)."""
    if not interval:
        return True
    return (interval or "").lower() not in ("one_time",)


async def _process_bricks_payment(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    body: CheckoutStartRequest,
    inv: Invoice,
    sub: Subscription,
    price_plan: PricePlan,
    cust: Customer | None,
    email: str,
    existing_customer: bool,
) -> CheckoutStartResponse:
    """Process payment using Brick token: create MP customer, save card, charge."""
    currency = (inv.currency or "BRL").upper()
    provider = await _get_payment_provider(db, inv.customer_id, ORG_ID, currency)

    if not isinstance(provider, MercadoPagoProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bricks payment is only available for Mercado Pago (BRL)",
        )

    payer_email = (body.payer_email or email).lower().strip()

    # 1. Create or get MP Customer
    try:
        mp_customer = provider.create_or_get_customer(email=payer_email, name=body.customer_name)
        mp_customer_id = str(mp_customer.get("id", ""))
        if mp_customer_id and cust and not cust.mp_customer_id:
            cust.mp_customer_id = mp_customer_id
            await db.flush()
    except ValueError:
        logger.warning("Failed to create/get MP customer for %s; proceeding with payment", email)
        mp_customer_id = ""

    # 2. Process payment (card or pix)
    try:
        payment = provider.create_payment(
            token=body.token,
            amount=float(inv.total),
            installments=body.installments,
            payment_method_id=body.payment_method_id,
            issuer_id=body.issuer_id,
            payer_email=payer_email,
            description=f"Invoice #{inv.id} - {(inv.line_items or [{}])[0].get('description', '')}",
            external_reference=str(inv.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    payment_status = (payment.get("status") or "").lower()
    payment_id = str(payment.get("id", ""))

    # 3. Save card to MP Customer for future recurring charges
    if mp_customer_id and body.token:
        try:
            card = provider.save_card(customer_id=mp_customer_id, card_token=body.token)
            card_id = str(card.get("id", ""))
            if card_id:
                logger.info("Saved card %s for MP customer %s", card_id, mp_customer_id)
        except Exception:
            logger.warning("Failed to save card for MP customer %s", mp_customer_id, exc_info=True)

    # 4. Update invoice and subscription based on payment status
    inv.external_id = payment_id
    if payment_status == "approved":
        inv.status = InvoiceStatus.PAID.value
        inv.paid_at = datetime.now(timezone.utc)
        sub.status = SubscriptionStatus.ACTIVE.value
        sub.start_date = datetime.now(timezone.utc)
        if _is_recurring_interval(price_plan.interval):
            sub.next_due_date = sub.start_date + timedelta(days=30)
        await reactivate_subscription_after_payment(db, sub.id, org_id=ORG_ID)
        await db.flush()
        # Same post-payment actions as webhooks: project, credentials email, notification
        cu_r = await db.execute(
            select(CustomerUser).where(CustomerUser.customer_id == inv.customer_id).limit(1)
        )
        cu = cu_r.scalar_one_or_none()
        if cu:
            await create_notification_and_maybe_send_email(
                db,
                background_tasks,
                customer_user_id=cu.id,
                channel="in_app,email",
                title="Pagamento confirmado",
                body=f"A fatura #{inv.id} foi paga.",
                recipient_email=cu.email,
                org_id=ORG_ID,
            )
        background_tasks.add_task(send_portal_credentials_after_payment, inv.customer_id, ORG_ID)
        background_tasks.add_task(_run_create_project_after_payment, inv.id)
    elif payment_status in ("pending", "in_process"):
        inv.status = InvoiceStatus.PENDING.value
    else:
        inv.status = InvoiceStatus.PENDING.value

    await db.flush()

    # Build user-friendly error message for rejected payments
    error_message = None
    if payment_status == "rejected":
        status_detail = payment.get("status_detail", "")
        error_messages = {
            "cc_rejected_bad_filled_card_number": "Número do cartão incorreto.",
            "cc_rejected_bad_filled_date": "Data de validade incorreta.",
            "cc_rejected_bad_filled_security_code": "Código de segurança incorreto.",
            "cc_rejected_bad_filled_other": "Dados do cartão incorretos.",
            "cc_rejected_call_for_authorize": "Ligue para a operadora do cartão para autorizar.",
            "cc_rejected_card_disabled": "Cartão desabilitado. Ligue para a operadora.",
            "cc_rejected_duplicated_payment": "Pagamento duplicado detectado.",
            "cc_rejected_high_risk": "Pagamento recusado por segurança.",
            "cc_rejected_insufficient_amount": "Saldo insuficiente.",
            "cc_rejected_max_attempts": "Limite de tentativas atingido. Tente outro cartão.",
            "cc_rejected_other_reason": "Pagamento recusado. Tente outro cartão.",
        }
        error_message = error_messages.get(status_detail, "Pagamento recusado. Verifique os dados e tente novamente.")

    # Extract Pix QR Code information if applicable
    qr_code_base64 = None
    qr_code = None
    ticket_url = None
    poi = payment.get("point_of_interaction", {})
    if poi:
        tx_data = poi.get("transaction_data", {})
        qr_code_base64 = tx_data.get("qr_code_base64")
        qr_code = tx_data.get("qr_code")
        ticket_url = tx_data.get("ticket_url")

    return CheckoutStartResponse(
        payment_status=payment_status,
        payment_id=payment_id,
        existing_customer=existing_customer,
        error_message=error_message,
        qr_code_base64=qr_code_base64,
        qr_code=qr_code,
        ticket_url=ticket_url,
    )
