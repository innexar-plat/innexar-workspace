"""Feature flags: get_flag(key) and RequireFeature dependency for portal routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.feature_flag import FeatureFlag


async def get_flag(db: AsyncSession, key: str) -> bool:
    """Return True if flag exists and enabled, False otherwise."""
    result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    flag = result.scalar_one_or_none()
    return flag is not None and flag.enabled


def require_portal_feature(flag_key: str):
    """Dependency factory: raise 404 if the given feature flag is disabled."""

    async def _check(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> None:
        if not await get_flag(db, flag_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This feature is not enabled",
            )

    return Depends(_check)
