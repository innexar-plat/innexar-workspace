"""Public billing routes: webhooks Stripe / Mercado Pago."""
import hmac
import json
import os
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.models.customer_user import CustomerUser
from app.modules.billing.models import Invoice
from app.modules.billing.post_payment import create_project_and_notify_after_payment
from app.modules.billing.provisioning import trigger_provisioning_if_needed
from app.modules.billing.service import process_webhook
from app.modules.customers.service import send_portal_credentials_after_payment
from app.modules.notifications.service import create_notification_and_maybe_send_email

router = APIRouter(tags=["public-webhooks"])


def _verify_mercadopago_webhook_signature(request: Request, body: bytes) -> bool:
    """Valida x-signature do MP (manifest + HMAC-SHA256). Se MP_WEBHOOK_SECRET não estiver definido, não valida."""
    secret = os.environ.get("MP_WEBHOOK_SECRET") or os.environ.get("MERCADOPAGO_WEBHOOK_SECRET")
    if not secret:
        return True
    x_sig = request.headers.get("x-signature")
    if not x_sig:
        return False
    ts_val: str | None = None
    v1_val: str | None = None
    for part in x_sig.split(","):
        key_val = part.strip().split("=", 1)
        if len(key_val) == 2:
            k, v = key_val[0].strip(), key_val[1].strip()
            if k == "ts":
                ts_val = v
            elif k == "v1":
                v1_val = v
    if not ts_val or not v1_val:
        return False
    x_request_id = request.headers.get("x-request-id") or ""
    data_id = request.query_params.get("data.id") or ""
    if not data_id and body:
        try:
            payload = json.loads(body)
            data_id = str((payload.get("data") or {}).get("id") or "")
        except Exception:
            pass
    if isinstance(data_id, str) and data_id.isalnum():
        data_id = data_id.lower()
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts_val};"
    expected = hmac.new(secret.encode(), manifest.encode(), sha256).hexdigest()
    return hmac.compare_digest(expected, v1_val)


async def _run_provisioning_after_payment(invoice_id: int) -> None:
    """Background: run provisioning with a new DB session."""
    async with AsyncSessionLocal() as db:
        try:
            await trigger_provisioning_if_needed(db, invoice_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _create_project_and_notify_after_payment(invoice_id: int) -> None:
    """Background: create project for site product and notify team."""
    async with AsyncSessionLocal() as db:
        try:
            await create_project_and_notify_after_payment(db, invoice_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@router.post("/webhooks/stripe")
async def webhook_stripe(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    body = await request.body()
    headers = dict(request.headers)
    ok, msg, paid_invoice_id = await process_webhook(db, "stripe", body, headers)
    if not ok and msg != "already_processed":
        return Response(content=msg, status_code=400)
    if ok and paid_invoice_id:
        inv = (await db.execute(select(Invoice).where(Invoice.id == paid_invoice_id).limit(1))).scalar_one_or_none()
        if inv:
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
                    title="Invoice paid",
                    body=f"Invoice #{paid_invoice_id} has been paid.",
                    recipient_email=cu.email,
                    org_id="innexar",
                )
            background_tasks.add_task(send_portal_credentials_after_payment, inv.customer_id, "innexar")
        background_tasks.add_task(_run_provisioning_after_payment, paid_invoice_id)
        background_tasks.add_task(_create_project_and_notify_after_payment, paid_invoice_id)
    return Response(content="ok", status_code=200)


def _is_mercadopago_test_notification(body: bytes) -> bool:
    """Painel MP 'Testar este URL' envia payload fixo com data.id=123456; pode não vir x-signature."""
    try:
        payload = json.loads(body)
        data_id = str((payload.get("data") or {}).get("id") or "")
        return payload.get("type") == "payment" and data_id == "123456"
    except Exception:
        return False


@router.post("/webhooks/mercadopago")
async def webhook_mercadopago(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    body = await request.body()
    # Notificação de teste do painel (id 123456): aceitar sem assinatura para o botão "Testar este URL" passar
    if not _is_mercadopago_test_notification(body) and not _verify_mercadopago_webhook_signature(request, body):
        return Response(content="invalid signature", status_code=401)
    headers = dict(request.headers)
    ok, msg, paid_invoice_id = await process_webhook(db, "mercadopago", body, headers)
    if not ok:
        return Response(content=msg, status_code=400)
    if ok and paid_invoice_id:
        inv = (await db.execute(select(Invoice).where(Invoice.id == paid_invoice_id).limit(1))).scalar_one_or_none()
        if inv:
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
                    body=f"A fatura #{paid_invoice_id} foi paga.",
                    recipient_email=cu.email,
                    org_id="innexar",
                )
            background_tasks.add_task(send_portal_credentials_after_payment, inv.customer_id, "innexar")
        background_tasks.add_task(_run_provisioning_after_payment, paid_invoice_id)
        background_tasks.add_task(_create_project_and_notify_after_payment, paid_invoice_id)
    return Response(content="ok", status_code=200)