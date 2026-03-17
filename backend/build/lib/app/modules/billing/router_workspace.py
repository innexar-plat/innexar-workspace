"""Workspace billing routes: products, price_plans, subscriptions, invoices."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.core.audit import log_audit
from app.core.database import AsyncSessionLocal, get_db
from app.core.rbac import RequirePermission
from app.models.customer import Customer
from app.models.user import User
from app.modules.billing.dependencies import require_billing_enabled
from app.modules.billing.enums import InvoiceStatus
from app.modules.billing.models import Invoice, PricePlan, Product, ProvisioningRecord, Subscription
from app.modules.billing.overdue import process_overdue_invoices, reactivate_subscription_after_payment, sync_subscription_status_to_hestia
from app.modules.billing.provisioning import trigger_provisioning_if_needed
from app.modules.billing.schemas import (
    InvoiceCreate,
    InvoiceResponse,
    LinkHestiaBody,
    PayBricksRequest,
    PayResponse,
    PricePlanCreate,
    PricePlanResponse,
    PricePlanUpdate,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    ProductWithPlansResponse,
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from app.modules.billing.service import (
    _get_payment_provider,
    create_manual_invoice,
    create_payment_attempt,
    generate_recurring_invoices,
    mark_invoice_paid,
    send_invoice_reminders,
)
from app.modules.notifications.service import create_notification_and_maybe_send_email
from app.providers.payments.mercadopago import MercadoPagoProvider

router = APIRouter(prefix="/billing", tags=["workspace-billing"])


async def _run_provisioning_after_payment(invoice_id: int) -> None:
    """Background: run provisioning with a new DB session."""
    async with AsyncSessionLocal() as db:
        try:
            await trigger_provisioning_if_needed(db, invoice_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def _invoice_to_response(inv: Invoice) -> dict:
    return {
        "id": inv.id,
        "customer_id": inv.customer_id,
        "subscription_id": inv.subscription_id,
        "status": inv.status,
        "due_date": inv.due_date,
        "paid_at": inv.paid_at,
        "total": float(inv.total),
        "currency": inv.currency,
        "line_items": inv.line_items,
        "external_id": inv.external_id,
        "created_at": inv.created_at,
    }


# ----- Products -----
@router.get("/products", response_model=list[ProductResponse] | list[ProductWithPlansResponse])
async def list_products(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
    with_plans: bool = False,
):
    r = await db.execute(select(Product).order_by(Product.id))
    products = list(r.scalars().all())
    if not with_plans:
        return products
    plans_r = await db.execute(select(PricePlan).order_by(PricePlan.id))
    all_plans = list(plans_r.scalars().all())
    by_product: dict[int, list[PricePlan]] = {}
    for pp in all_plans:
        by_product.setdefault(pp.product_id, []).append(pp)
    return [
        ProductWithPlansResponse(
            **ProductResponse.model_validate(p).model_dump(),
            price_plans=[PricePlanResponse.model_validate(pp) for pp in by_product.get(p.id, [])],
        )
        for p in products
    ]


@router.post("/products", response_model=ProductResponse, status_code=201)
async def create_product(
    body: ProductCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    p = Product(
        name=body.name,
        description=body.description,
        is_active=body.is_active,
        provisioning_type=body.provisioning_type,
        hestia_package=body.hestia_package,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    return p


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(select(Product).where(Product.id == product_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    body: ProductUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(select(Product).where(Product.id == product_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.is_active is not None:
        p.is_active = body.is_active
    if body.provisioning_type is not None:
        p.provisioning_type = body.provisioning_type
    if body.hestia_package is not None:
        p.hestia_package = body.hestia_package
    await db.flush()
    await db.refresh(p)
    return p


# ----- Price plans -----
@router.get("/price-plans", response_model=list[PricePlanResponse])
async def list_price_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
    product_id: int | None = None,
):
    q = select(PricePlan)
    if product_id is not None:
        q = q.where(PricePlan.product_id == product_id)
    r = await db.execute(q.order_by(PricePlan.id))
    return list(r.scalars().all())


@router.post("/price-plans", response_model=PricePlanResponse, status_code=201)
async def create_price_plan(
    body: PricePlanCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    pp = PricePlan(
        product_id=body.product_id,
        name=body.name,
        interval=body.interval,
        amount=body.amount,
        currency=body.currency,
    )
    db.add(pp)
    await db.flush()
    await db.refresh(pp)
    return pp


@router.patch("/price-plans/{plan_id}", response_model=PricePlanResponse)
async def update_price_plan(
    plan_id: int,
    body: PricePlanUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(select(PricePlan).where(PricePlan.id == plan_id))
    pp = r.scalar_one_or_none()
    if not pp:
        raise HTTPException(status_code=404, detail="Price plan not found")
    if body.name is not None:
        pp.name = body.name
    if body.interval is not None:
        pp.interval = body.interval
    if body.amount is not None:
        pp.amount = body.amount
    if body.currency is not None:
        pp.currency = body.currency
    await db.flush()
    await db.refresh(pp)
    return pp


# ----- Subscriptions -----
@router.get("/subscriptions", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
    customer_id: int | None = None,
):
    q = select(Subscription)
    if customer_id is not None:
        q = q.where(Subscription.customer_id == customer_id)
    r = await db.execute(q.order_by(Subscription.id.desc()))
    return list(r.scalars().all())


@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def create_subscription(
    body: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    sub = Subscription(
        customer_id=body.customer_id,
        product_id=body.product_id,
        price_plan_id=body.price_plan_id,
        status=body.status,
        start_date=body.start_date,
        next_due_date=body.next_due_date,
    )
    db.add(sub)
    await db.flush()
    await log_audit(
        db,
        entity="subscription",
        entity_id=str(sub.id),
        action="create",
        actor_type="staff",
        actor_id=str(current.id),
        org_id=current.org_id or "innexar",
    )
    await db.refresh(sub)
    return sub


@router.patch("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: int,
    body: SubscriptionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(select(Subscription).where(Subscription.id == subscription_id).limit(1))
    sub = r.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.status is not None:
        new_status = body.status
        sub.status = new_status
        org_id = current.org_id or "innexar"
        await sync_subscription_status_to_hestia(db, subscription_id, new_status, org_id)
    if body.start_date is not None:
        sub.start_date = body.start_date
    if body.end_date is not None:
        sub.end_date = body.end_date
    if body.next_due_date is not None:
        sub.next_due_date = body.next_due_date
    await db.flush()
    await db.refresh(sub)
    return sub


@router.post("/subscriptions/{subscription_id}/link-hestia", status_code=status.HTTP_201_CREATED)
async def link_hestia_user(
    subscription_id: int,
    body: LinkHestiaBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    """Link an existing Hestia user to a subscription (creates ProvisioningRecord only; no Hestia API calls)."""
    r = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id).limit(1)
    )
    sub = r.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.invoice_id is not None:
        inv_r = await db.execute(
            select(Invoice).where(
                Invoice.id == body.invoice_id,
                Invoice.subscription_id == subscription_id,
            ).limit(1)
        )
        if inv_r.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=400,
                detail="invoice_id must belong to this subscription or be omitted",
            )
    rec = ProvisioningRecord(
        subscription_id=subscription_id,
        invoice_id=body.invoice_id,
        provider="hestia",
        external_user=body.hestia_username.strip(),
        domain=body.domain.strip(),
        site_url=f"https://{body.domain.strip()}",
        panel_login=body.hestia_username.strip(),
        panel_url=None,
        panel_password_encrypted=None,
        status="provisioned",
        provisioned_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    await db.flush()
    await db.refresh(rec)
    return {"ok": True, "provisioning_record_id": rec.id}


# ----- Invoices -----
@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_invoices(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
    customer_id: int | None = None,
    status: str | None = None,
):
    q = select(Invoice)
    if customer_id is not None:
        q = q.where(Invoice.customer_id == customer_id)
    if status is not None:
        q = q.where(Invoice.status == status)
    r = await db.execute(q.order_by(Invoice.id.desc()))
    return [_invoice_to_response(inv) for inv in r.scalars().all()]


@router.post("/invoices", response_model=InvoiceResponse, status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    inv = await create_manual_invoice(
        db,
        customer_id=body.customer_id,
        due_date=body.due_date,
        total=body.total,
        currency=body.currency,
        line_items=body.line_items,
    )
    inv.subscription_id = body.subscription_id
    await db.flush()
    await db.refresh(inv)
    return _invoice_to_response(inv)


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:read"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = r.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_to_response(inv)


@router.post("/invoices/{invoice_id}/payment-link")
async def invoice_payment_link(
    invoice_id: int,
    success_url: str,
    cancel_url: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    try:
        res = await create_payment_attempt(db, invoice_id, success_url, cancel_url)
        return {"payment_url": res.payment_url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/invoices/{invoice_id}/pay-bricks", response_model=PayResponse)
async def invoice_pay_bricks(
    invoice_id: int,
    body: PayBricksRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    """Pay invoice with Bricks (card/Pix). Staff initiates; payer_email is the customer email. Returns payment_status, qr_code for Pix, etc."""
    org_id = current.org_id or "innexar"
    r = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = r.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == InvoiceStatus.PAID.value:
        raise HTTPException(status_code=400, detail="Invoice already paid")

    currency = (inv.currency or "BRL").upper()
    provider = await _get_payment_provider(db, inv.customer_id, org_id, currency)
    if not isinstance(provider, MercadoPagoProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bricks is only available for Mercado Pago (BRL)",
        )
    payer_email = (body.payer_email or "").strip().lower()
    if not payer_email:
        raise HTTPException(status_code=400, detail="payer_email is required")

    cust = (
        await db.execute(
            select(Customer).where(Customer.id == inv.customer_id).options(selectinload(Customer.users))
        )
    ).scalar_one_or_none()
    if cust and not cust.mp_customer_id:
        try:
            mp_customer = provider.create_or_get_customer(
                email=payer_email, name=body.customer_name or cust.name
            )
            cust.mp_customer_id = str(mp_customer.get("id", ""))
            await db.flush()
        except ValueError:
            pass

    desc = f"Invoice #{inv.id}"
    if inv.line_items and isinstance(inv.line_items, list) and inv.line_items:
        first = inv.line_items[0]
        if isinstance(first, dict):
            desc = str(first.get("description", desc))
    try:
        payment = provider.create_payment(
            token=body.token,
            amount=float(inv.total),
            installments=body.installments,
            payment_method_id=body.payment_method_id,
            issuer_id=body.issuer_id,
            payer_email=payer_email,
            description=desc,
            external_reference=str(inv.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    payment_status = (payment.get("status") or "").lower()
    payment_id = str(payment.get("id", ""))

    inv.external_id = payment_id
    if payment_status == "approved":
        inv.status = InvoiceStatus.PAID.value
        inv.paid_at = datetime.now(timezone.utc)
        if inv.subscription_id:
            sub_r = await db.execute(
                select(Subscription).where(Subscription.id == inv.subscription_id).limit(1)
            )
            sub = sub_r.scalar_one_or_none()
            if sub:
                await reactivate_subscription_after_payment(db, sub.id, org_id=org_id)
        await db.flush()
        background_tasks.add_task(_run_provisioning_after_payment, inv.id)
        if cust and cust.users:
            for cu in cust.users:
                await create_notification_and_maybe_send_email(
                    db,
                    background_tasks,
                    customer_user_id=cu.id,
                    channel="in_app,email",
                    title="Pagamento confirmado",
                    body=f"A fatura #{inv.id} foi paga.",
                    recipient_email=cu.email,
                    org_id=org_id,
                )
    else:
        inv.status = InvoiceStatus.PENDING.value
        await db.flush()

    error_message = None
    if payment_status == "rejected":
        status_detail = payment.get("status_detail", "")
        error_messages = {
            "cc_rejected_bad_filled_card_number": "Número do cartão incorreto.",
            "cc_rejected_duplicated_payment": "Pagamento duplicado detectado.",
            "cc_rejected_insufficient_amount": "Saldo insuficiente.",
            "cc_rejected_other_reason": "Pagamento recusado. Tente outro cartão.",
        }
        error_message = error_messages.get(status_detail, "Pagamento recusado. Tente novamente.")

    poi = payment.get("point_of_interaction", {}) or {}
    tx_data = poi.get("transaction_data", {}) or {}
    return PayResponse(
        payment_url="",
        attempt_id=0,
        payment_status=payment_status,
        payment_id=payment_id,
        error_message=error_message,
        qr_code_base64=tx_data.get("qr_code_base64"),
        qr_code=tx_data.get("qr_code"),
        ticket_url=tx_data.get("ticket_url"),
    )


@router.post("/invoices/{invoice_id}/mark-paid")
async def invoice_mark_paid(
    invoice_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
):
    """Mark invoice as paid (manual override). Activates subscription and queues provisioning if applicable."""
    org_id = current.org_id or "innexar"
    paid_id = await mark_invoice_paid(
        db,
        invoice_id,
        actor_type="staff",
        actor_id=str(current.id),
        org_id=org_id,
    )
    if paid_id is None:
        raise HTTPException(
            status_code=400,
            detail="Invoice not found or already paid",
        )
    background_tasks.add_task(_run_provisioning_after_payment, paid_id)
    return {"ok": True, "invoice_id": paid_id}


@router.post("/process-overdue")
async def billing_process_overdue(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
):
    """Run overdue processor: suspend Hestia users and set subscriptions SUSPENDED for unpaid invoices past grace period. Call from cron."""
    org_id = current.org_id or "innexar"
    count = await process_overdue_invoices(db, org_id=org_id)
    return {"processed": count}


@router.post("/generate-recurring-invoices")
async def billing_generate_recurring_invoices(
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
    current: Annotated[User, Depends(RequirePermission("billing:write"))],
    __: Annotated[None, Depends(require_billing_enabled)],
    days_before_due: int = 0,
    send_reminders: bool = False,
):
    """Generate next invoice for active subscriptions whose next_due_date is due (or within days_before_due).
    If send_reminders=True, also send email + in-portal reminder for PENDING invoices due in the next 2 days.
    Example cron: POST ?days_before_due=2&send_reminders=true to generate 2 days early and send reminders."""
    org_id = current.org_id or "innexar"
    count = await generate_recurring_invoices(
        db, org_id=org_id, days_before_due=days_before_due
    )
    reminded = 0
    if send_reminders:
        reminded = await send_invoice_reminders(
            db, background_tasks, org_id=org_id, days_ahead=2
        )
    return {"generated": count, "reminders_sent": reminded}
