"""Workspace project files: list, download (staff can access any project)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.user import User
from app.modules.files.schemas import ProjectFileResponse
from app.modules.files.service import (
    get_file_content,
    get_project_file,
    list_project_files,
)
from app.modules.projects.models import Project

router = APIRouter(prefix="/projects", tags=["workspace-project-files"])


@router.get("/{project_id}/files", response_model=list[ProjectFileResponse])
async def list_project_files_workspace(
    project_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
):
    """List files for a project (workspace staff)."""
    r = await db.execute(select(Project).where(Project.id == project_id))
    if not r.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
    files = await list_project_files(db, project_id)
    return files


@router.get("/{project_id}/files/{file_id}/download")
async def download_project_file_workspace(
    project_id: int,
    file_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("projects:read"))],
):
    """Download a file from a project (workspace staff)."""
    r = await db.execute(select(Project).where(Project.id == project_id))
    if not r.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
    pf = await get_project_file(db, file_id, project_id=project_id)
    if not pf:
        raise HTTPException(status_code=404, detail="File not found")
    content, content_type = await get_file_content(pf.path_key)
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{pf.filename}"',
        },
    )
