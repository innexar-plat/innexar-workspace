"""Customer auth: dependency get_current_customer for portal routes."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import decode_token_customer
from app.models.customer_user import CustomerUser

security_customer = HTTPBearer(auto_error=False)


async def get_current_customer(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_customer)] = None,
) -> CustomerUser:
    """Validate Bearer token (customer) and return CustomerUser. Raise 401 if invalid."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido. Use: Authorization: Bearer <token>",
        )
    payload = decode_token_customer(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
        )
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    result = await db.execute(
        select(CustomerUser)
        .options(selectinload(CustomerUser.customer))
        .where(CustomerUser.id == int(sub))
    )
    customer_user = result.scalar_one_or_none()
    if customer_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado")
    return customer_user
