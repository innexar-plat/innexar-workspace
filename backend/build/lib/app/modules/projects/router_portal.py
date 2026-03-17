"""Portal projects routes: list projects for current customer."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_customer import get_current_customer
from app.core.database import get_db
from app.core.feature_flags import require_portal_feature
from app.models.customer_user import CustomerUser
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectResponse

router = APIRouter(prefix="/projects", tags=["portal-projects"])


@router.get("", response_model=list[ProjectResponse])
async def list_my_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.projects.enabled")],
):
    """List projects for the current customer."""
    r = await db.execute(
        select(Project)
        .where(Project.customer_id == current.customer_id)
        .order_by(Project.id.desc())
    )
    return list(r.scalars().all())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_my_project(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CustomerUser, Depends(get_current_customer)],
    _: Annotated[None, require_portal_feature("portal.projects.enabled")],
):
    """Get project detail (only if owned by current customer)."""
    r = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.customer_id == current.customer_id,
        )
    )
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p
