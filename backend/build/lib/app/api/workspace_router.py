"""Workspace API: staff-only routes (login, me, forgot/reset/change password)."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status  # noqa: I001
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_staff import get_current_staff
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import create_token_staff, hash_password, verify_password
from app.models.staff_password_reset import StaffPasswordResetToken
from app.models.user import User
from app.providers.email.loader import get_email_provider
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    StaffLoginResponse,
    StaffMeResponse,
    StaffResetPasswordRequest,
    LoginRequest,
)

router = APIRouter()


@router.post("/auth/staff/login", response_model=StaffLoginResponse)
async def staff_login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StaffLoginResponse:
    """Workspace: staff login. Returns JWT for /api/workspace/*."""
    result = await db.execute(
        select(User).where(User.email == body.email.lower()).limit(1)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    token = create_token_staff(user.id)
    return StaffLoginResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
    )


@router.get("/me", response_model=StaffMeResponse)
async def staff_me(
    current_user: Annotated[User, Depends(get_current_staff)],
) -> StaffMeResponse:
    """Workspace: current staff profile."""
    return StaffMeResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        org_id=current_user.org_id,
    )


async def _send_staff_reset_email(
    recipient_email: str, reset_link: str, org_id: str
) -> None:
    """Send staff password reset email. Uses new DB session for provider lookup."""
    async with AsyncSessionLocal() as db:
        provider = await get_email_provider(db, org_id=org_id)
        if provider:
            subject = "Redefinir senha do painel administrativo"
            body = (
                f"Você solicitou a redefinição de senha do painel.\n\n"
                f"Acesse o link abaixo para definir uma nova senha (válido por 24 horas):\n\n"
                f"{reset_link}\n\n"
                "Se você não solicitou isso, ignore este e-mail."
            )
            provider.send(recipient_email, subject, body, None)


@router.post("/auth/staff/forgot-password", status_code=200)
async def staff_forgot_password(
    body: ForgotPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Public: request staff password reset. Sends email with link if account exists. Always 200."""
    email_lower = body.email.lower().strip()
    r = await db.execute(select(User).where(User.email == email_lower).limit(1))
    user = r.scalar_one_or_none()
    if user:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        row = StaffPasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at,
        )
        db.add(row)
        await db.flush()
        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
        reset_link = f"{base_url}/workspace/reset-password?token={token}"
        org_id = user.org_id or "innexar"
        background_tasks.add_task(
            _send_staff_reset_email, email_lower, reset_link, org_id
        )
    return {
        "message": "If an account exists with this email, you will receive a reset link."
    }


@router.post("/auth/staff/reset-password", status_code=200)
async def staff_reset_password(
    body: StaffResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Public: set new staff password using token from email. Invalidates token."""
    if not body.token.strip() or len(body.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token e senha (mín. 6 caracteres) são obrigatórios",
        )
    now = datetime.now(timezone.utc)
    r = await db.execute(
        select(StaffPasswordResetToken)
        .where(
            StaffPasswordResetToken.token == body.token.strip(),
            StaffPasswordResetToken.expires_at > now,
        )
        .limit(1)
    )
    row = r.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido ou expirado",
        )
    user_r = await db.execute(select(User).where(User.id == row.user_id).limit(1))
    user = user_r.scalar_one_or_none()
    if not user:
        await db.execute(
            delete(StaffPasswordResetToken).where(StaffPasswordResetToken.id == row.id)
        )
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido ou expirado",
        )
    user.password_hash = hash_password(body.new_password)
    await db.execute(
        delete(StaffPasswordResetToken).where(StaffPasswordResetToken.user_id == user.id)
    )
    await db.flush()
    return {"message": "Senha atualizada. Faça login novamente."}


@router.patch("/me/password", status_code=200)
async def staff_change_password(
    body: ChangePasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_staff)],
) -> dict[str, str]:
    """Workspace: change password for current staff (current + new password)."""
    if len(body.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nova senha deve ter no mínimo 6 caracteres",
        )
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual incorreta",
        )
    current_user.password_hash = hash_password(body.new_password)
    await db.flush()
    return {"message": "Senha alterada com sucesso."}
