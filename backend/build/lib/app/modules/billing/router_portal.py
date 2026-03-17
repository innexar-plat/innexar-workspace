"""Portal billing routes: list invoices, pay, download (print-friendly HTML)."""
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_customer import get_current_customer
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.debug_log import debug_log
from app.models.customer import Customer
from app.models.customer_user import CustomerUser
from app.modules.billing.dependencies import require_billing_enabled
from app.modules.billing.enums import InvoiceStatus
from app.modules.billing.models import Invoice, Subscription
from app.modules.billing.overdue import reactivate_subscription_after_payment
from app.modules.billing.provisioning import trigger_provisioning_if_needed
from app.modules.billing.schemas import InvoiceResponse, PayRequest, PayResponse
from app.modules.billing.service import _get_payment_provider, create_payment_attempt
from app.modules.notifications.service import create_notification_and_maybe_send_email
from app.providers.payments.mercadopago import MercadoPagoProvider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portal-billing"])
ORG_ID = "innexar"


async def _parse_pay_body(request: Request) -> PayRequest:
    """Parse optional body so POST with empty or missing body still works (avoids 422)."""
    # #region agent log
    try:
        raw = await request.body()
        debug_log(
            "router_portal._parse_pay_body",
            "Body received",
            {"raw_len": len(raw), "raw_stripped_empty": not (raw and raw.strip())},
            "A",
        )
        if not raw or not raw.strip():
            return PayRequest()
        out = PayRequest.model_validate_json(raw)
        debug_log(
            "router_portal._parse_pay_body",
            "Body parsed",
            {"has_payment_method_id": bool(out.payment_method_id)},
            "A",
        )
        return out
    except Exception as e:
        debug_log("router_portal._parse_pay_body", "Parse exception", {"type": type(e).__name__}, "A")
        return PayRequest()
    # #endregion


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


@router.get("/invoices", response_model=list[InvoiceResponse])
async def list_my_invoices(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, Depends(require_billing_enabled)],
):
    q = select(Invoice).where(Invoice.customer_id == current.customer_id).order_by(Invoice.id.desc())
    r = await db.execute(q)
    return [_invoice_to_response(inv) for inv in r.scalars().all()]


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_my_invoice(
    invoice_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.customer_id == current.customer_id,
        )
    )
    inv = r.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_to_response(inv)


@router.post("/invoices/{invoice_id}/pay", response_model=PayResponse)
async def pay_invoice(
    invoice_id: int,
    payload: Annotated[PayRequest, Depends(_parse_pay_body)],
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, Depends(require_billing_enabled)],
):
    r = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.customer_id == current.customer_id,
        )
    )
    inv = r.scalar_one_or_none()
    # #region agent log
    debug_log(
        "router_portal.pay_invoice",
        "Invoice fetch",
        {"invoice_id": invoice_id, "found": inv is not None, "status": getattr(inv, "status", None)},
        "B",
    )
    # #endregion
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == InvoiceStatus.PAID.value:
        raise HTTPException(status_code=400, detail="Invoice already paid")

    # Bricks: pay with card/Pix token inline (same flow as checkout)
    if payload.payment_method_id:
        return await _pay_invoice_bricks(
            db, background_tasks, inv, current, payload
        )

    # Checkout Pro: use body URLs or defaults so portal can POST with empty body or no body
    base = (
        (getattr(settings, "PORTAL_URL", None) or "").strip()
        or (getattr(settings, "FRONTEND_URL", None) or "").strip()
        or "https://portal.innexar.com.br"
    ).rstrip("/")
    success_url = (payload.success_url or "").strip() or f"{base}/payment/success"
    cancel_url = (payload.cancel_url or "").strip() or f"{base}/payment/cancel"
    # #region agent log
    debug_log(
        "router_portal.pay_invoice",
        "Before create_payment_attempt",
        {"base": base, "success_url": success_url[:80]},
        "C",
    )
    # #endregion
    try:
        res = await create_payment_attempt(
            db,
            invoice_id=inv.id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=current.email,
        )
    except ValueError as e:
        detail = str(e)
        # #region agent log
        debug_log(
            "router_portal.pay_invoice",
            "ValueError from create_payment_attempt",
            {"detail": detail[:200], "invoice_id": invoice_id},
            "D",
        )
        # #endregion
        logger.warning("Portal pay invoice %s failed: %s", invoice_id, detail)
        # MP 403 PolicyAgent UNAUTHORIZED: token/policy issue, not client error → 503
        if "403" in detail and ("PolicyAgent" in detail or "UNAUTHORIZED" in detail):
            raise HTTPException(
                status_code=503,
                detail="Pagamento temporariamente indisponível. Verifique a configuração do Mercado Pago (token e permissões) no servidor.",
            )
        raise HTTPException(status_code=400, detail=detail)
    from app.modules.billing.models import PaymentAttempt
    r2 = await db.execute(
        select(PaymentAttempt).where(PaymentAttempt.invoice_id == invoice_id).order_by(PaymentAttempt.id.desc()).limit(1)
    )
    attempt = r2.scalar_one_or_none()
    return PayResponse(payment_url=res.payment_url, attempt_id=attempt.id if attempt else 0)


async def _pay_invoice_bricks(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    inv: Invoice,
    current: CustomerUser,
    body: PayRequest,
) -> PayResponse:
    """Pay invoice with Bricks (Mercado Pago card/Pix token)."""
    currency = (inv.currency or "BRL").upper()
    provider = await _get_payment_provider(db, inv.customer_id, ORG_ID, currency)
    if not isinstance(provider, MercadoPagoProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bricks payment is only available for Mercado Pago (BRL)",
        )
    payer_email = (body.payer_email or current.email or "").lower().strip()
    if not payer_email:
        raise HTTPException(status_code=400, detail="payer_email or login email required")

    cust = (await db.execute(select(Customer).where(Customer.id == inv.customer_id).limit(1))).scalar_one_or_none()
    if cust and not cust.mp_customer_id:
        try:
            mp_customer = provider.create_or_get_customer(
                email=payer_email, name=body.customer_name or current.email
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
                await reactivate_subscription_after_payment(db, sub.id, org_id=ORG_ID)
        await db.flush()

        async def _run_provisioning(invoice_id: int) -> None:
            async with AsyncSessionLocal() as session:
                try:
                    await trigger_provisioning_if_needed(session, invoice_id)
                    await session.commit()
                except Exception:
                    await session.rollback()
        background_tasks.add_task(_run_provisioning, inv.id)

        await create_notification_and_maybe_send_email(
            db,
            background_tasks,
            customer_user_id=current.id,
            channel="in_app,email",
            title="Pagamento confirmado",
            body=f"A fatura #{inv.id} foi paga.",
            recipient_email=current.email,
            org_id=ORG_ID,
        )
    else:
        inv.status = InvoiceStatus.PENDING.value
        await db.flush()

    error_message = None
    if payment_status == "rejected":
        status_detail = payment.get("status_detail", "")
        error_messages = {
            "cc_rejected_bad_filled_card_number": "Número do cartão incorreto.",
            "cc_rejected_bad_filled_date": "Data de validade incorreta.",
            "cc_rejected_bad_filled_security_code": "Código de segurança incorreto.",
            "cc_rejected_duplicated_payment": "Pagamento duplicado detectado.",
            "cc_rejected_insufficient_amount": "Saldo insuficiente.",
            "cc_rejected_other_reason": "Pagamento recusado. Tente outro cartão.",
        }
        error_message = error_messages.get(status_detail, "Pagamento recusado. Verifique os dados e tente novamente.")

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


@router.get("/invoices/{invoice_id}/download", response_class=HTMLResponse)
async def download_invoice_html(
    invoice_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, Depends(require_billing_enabled)],
) -> HTMLResponse:
    """Portal: return print-friendly HTML for invoice. User can print to PDF (Ctrl+P -> Save as PDF)."""
    r = await db.execute(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.customer_id == current.customer_id,
        )
    )
    inv = r.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    due = inv.due_date.strftime("%d/%m/%Y") if inv.due_date else ""
    paid = inv.paid_at.strftime("%d/%m/%Y") if inv.paid_at else ""
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <title>Fatura #{inv.id}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 2rem auto; padding: 1rem; color: #1e293b; }}
    h1 {{ font-size: 1.25rem; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
    th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid #e2e8f0; }}
    .meta {{ color: #64748b; font-size: 0.875rem; }}
    @media print {{ body {{ margin: 0; }} }}
  </style>
</head>
<body>
  <h1>Fatura #{inv.id}</h1>
  <p class="meta">Cliente ID: {inv.customer_id}</p>
  <table>
    <tr><th>Status</th><td>{inv.status}</td></tr>
    <tr><th>Vencimento</th><td>{due}</td></tr>
    <tr><th>Pago em</th><td>{paid or "—"}</td></tr>
    <tr><th>Total</th><td><strong>{inv.currency} {float(inv.total):,.2f}</strong></td></tr>
  </table>
  <p class="meta">Para salvar como PDF: use o menu do navegador (Ctrl+P ou Cmd+P) e escolha "Salvar como PDF".</p>
</body>
</html>"""
    return HTMLResponse(content=html)
