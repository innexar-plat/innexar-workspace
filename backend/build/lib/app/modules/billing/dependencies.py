"""Billing route dependencies: feature flag check."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.feature_flags import get_flag


async def require_billing_enabled(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Raise 404 if billing/portal invoices are disabled (aligned with GET /api/portal/me/features)."""
    if not (
        await get_flag(db, "billing.enabled")
        or await get_flag(db, "portal.invoices.enabled")
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )
