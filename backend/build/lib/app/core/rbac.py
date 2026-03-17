"""RBAC: require_permission dependency for workspace routes."""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth_staff import get_current_staff
from app.core.database import get_db
from app.models.permission import Permission
from app.models.role import Role
from app.models.user import User


async def get_user_permission_slugs(db: AsyncSession, user_id: int) -> set[str]:
    """Return set of permission slugs for user (via roles). Admin role has all permissions."""
    result = await db.execute(
        select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        return set()
    if any(role.slug == "admin" for role in user.roles):
        all_perms = await db.execute(select(Permission.slug))
        return set(all_perms.scalars().all())
    slugs: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            slugs.add(perm.slug)
    return slugs


def RequirePermission(permission: str):
    """Return a Depends() that requires the given permission. Use: Depends(RequirePermission('billing:read'))."""

    async def _check(
        db: Annotated[AsyncSession, Depends(get_db)],
        current: Annotated[User, Depends(get_current_staff)],
    ) -> User:
        slugs = await get_user_permission_slugs(db, current.id)
        if permission not in slugs:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permissão necessária: {permission}",
            )
        return current

    return _check
