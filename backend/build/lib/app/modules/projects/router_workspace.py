"""Workspace projects routes."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.user import User
from app.modules.projects.models import Project
from app.modules.projects.schemas import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["workspace-projects"])


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
):
    """List projects (workspace)."""
    r = await db.execute(select(Project).order_by(Project.id.desc()))
    return list(r.scalars().all())


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:write"))],
):
    """Create project."""
    p = Project(
        customer_id=body.customer_id,
        name=body.name,
        status=body.status,
        subscription_id=body.subscription_id,
    )
    db.add(p)
    await db.flush()
    await db.refresh(p)
    return p


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
):
    """Get project by id."""
    r = await db.execute(select(Project).where(Project.id == project_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:write"))],
):
    """Update project."""
    r = await db.execute(select(Project).where(Project.id == project_id))
    p = r.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.name is not None:
        p.name = body.name
    if body.status is not None:
        allowed = (
            "aguardando_briefing",
            "briefing_recebido",
            "design",
            "desenvolvimento",
            "revisao",
            "entrega",
            "projeto_concluido",
            "active",
            "delivered",
            "cancelled",
        )
        if body.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(allowed)}",
            )
        p.status = body.status
    if body.expected_delivery_at is not None:
        p.expected_delivery_at = body.expected_delivery_at
    await db.flush()
    await db.refresh(p)
    return p

