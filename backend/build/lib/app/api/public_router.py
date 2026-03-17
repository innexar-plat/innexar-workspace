"""Public API: unauthenticated routes (login, webhooks, web-to-lead, etc.)."""
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, HTTPException, status  # noqa: I001
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import create_token_customer, hash_password, verify_password
from app.models.customer_password_reset import CustomerPasswordResetToken
from app.models.customer_user import CustomerUser
from app.modules.crm.models import Contact, Lead
from app.providers.email.loader import get_email_provider
from app.schemas.auth import CustomerLoginResponse, LoginRequest

router = APIRouter()

# Simple in-memory rate limit for web-to-lead (per email and per IP, per hour)
_WEBTOLEAD_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_EMAIL = 5
_RATE_LIMIT_IP = 20
_RATE_WINDOW = 3600.0  # 1 hour


def _rate_limit_check(key: str, limit: int) -> bool:
    """Return True if under limit (allow), False if over limit (reject)."""
    now = time.monotonic()
    # Use monotonic time; we store relative timestamps and prune old
    _WEBTOLEAD_RATE[key] = [t for t in _WEBTOLEAD_RATE[key] if now - t < _RATE_WINDOW]
    if len(_WEBTOLEAD_RATE[key]) >= limit:
        return False
    _WEBTOLEAD_RATE[key].append(now)
    return True


class WebToLeadRequest(BaseModel):
    """Web-to-lead: name, email, phone, optional message/source."""

    name: str
    email: EmailStr
    phone: str | None = None
    message: str | None = None
    source: str | None = None


@router.post("/auth/customer/login", response_model=CustomerLoginResponse)
async def customer_login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerLoginResponse:
    """Public: customer login (portal). Returns JWT for /api/portal/*."""
    result = await db.execute(
        select(CustomerUser).where(CustomerUser.email == body.email.lower())
    )
    customer_user = result.scalar_one_or_none()
    if customer_user is None or not verify_password(body.password, customer_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    token = create_token_customer(customer_user.id)
    return CustomerLoginResponse(
        access_token=token,
        customer_user_id=customer_user.id,
        customer_id=customer_user.customer_id,
        email=customer_user.email,
    )


class ForgotPasswordRequest(BaseModel):
    """Portal: forgot password (email)."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Portal: reset password (token from email link + new password)."""

    token: str
    new_password: str


async def _send_reset_email(recipient_email: str, reset_link: str, org_id: str) -> None:
    """Send password reset email. Uses new DB session for provider lookup."""
    async with AsyncSessionLocal() as db:
        provider = await get_email_provider(db, org_id=org_id)
        if provider:
            subject = "Redefinir senha do portal"
            body = (
                f"Você solicitou a redefinição de senha.\n\n"
                f"Acesse o link abaixo para definir uma nova senha (válido por 24 horas):\n\n"
                f"{reset_link}\n\n"
                "Se você não solicitou isso, ignore este e-mail."
            )
            provider.send(recipient_email, subject, body, None)


@router.post("/auth/customer/forgot-password", status_code=200)
async def customer_forgot_password(
    body: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Public: request password reset. Sends email with link if account exists. Always returns 200."""
    email_lower = body.email.lower().strip()
    r = await db.execute(select(CustomerUser).where(CustomerUser.email == email_lower).limit(1))
    cu = r.scalar_one_or_none()
    if cu:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        row = CustomerPasswordResetToken(
            customer_user_id=cu.id,
            token=token,
            expires_at=expires_at,
        )
        db.add(row)
        await db.flush()
        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
        reset_link = f"{base_url}/portal/reset-password?token={token}"
        background_tasks.add_task(_send_reset_email, email_lower, reset_link, "innexar")
    return {"message": "If an account exists with this email, you will receive a reset link."}


@router.post("/auth/customer/reset-password", status_code=200)
async def customer_reset_password(
    body: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Public: set new password using token from email. Invalidates token."""
    if not body.token.strip() or len(body.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token and password (min 6 characters) required",
        )
    now = datetime.now(timezone.utc)
    r = await db.execute(
        select(CustomerPasswordResetToken)
        .where(
            CustomerPasswordResetToken.token == body.token.strip(),
            CustomerPasswordResetToken.expires_at > now,
        )
        .limit(1)
    )
    row = r.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    cu_r = await db.execute(select(CustomerUser).where(CustomerUser.id == row.customer_user_id).limit(1))
    cu = cu_r.scalar_one_or_none()
    if not cu:
        await db.execute(delete(CustomerPasswordResetToken).where(CustomerPasswordResetToken.id == row.id))
        await db.flush()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    cu.password_hash = hash_password(body.new_password)
    await db.execute(delete(CustomerPasswordResetToken).where(CustomerPasswordResetToken.customer_user_id == cu.id))
    await db.flush()
    return {"message": "Password updated. You can now log in."}


@router.post("/web-to-lead", status_code=201)
async def web_to_lead(
    body: WebToLeadRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    """Public: create Contact (lead) from form. Rate limited by IP and email."""
    email_key = f"email:{body.email.lower().strip()}"
    client_host = request.client.host if request.client else "unknown"
    ip_key = f"ip:{client_host}"
    if not _rate_limit_check(email_key, _RATE_LIMIT_EMAIL):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many submissions for this email",
        )
    if not _rate_limit_check(ip_key, _RATE_LIMIT_IP):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many submissions from this IP",
        )
    contact = Contact(
        org_id="innexar",
        customer_id=None,
        name=body.name,
        email=body.email,
        phone=body.phone,
    )
    db.add(contact)
    await db.flush()

    origem = (body.source or "site").strip() or "site"
    lead = Lead(
        org_id="innexar",
        nome=body.name,
        email=body.email,
        telefone=body.phone,
        origem=origem,
        status="novo",
        contact_id=contact.id,
    )
    db.add(lead)
    await db.flush()

    await log_audit(
        db,
        entity="contact",
        entity_id=str(contact.id),
        action="web_to_lead",
        actor_type="public",
        actor_id=client_host,
        org_id="innexar",
        payload={"message": body.message, "source": body.source, "lead_id": lead.id},
    )
    return {"id": contact.id, "lead_id": lead.id}
