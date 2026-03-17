"""Files API schemas."""
from datetime import datetime

from pydantic import BaseModel


class ProjectFileResponse(BaseModel):
    """Project file metadata (list/detail)."""

    id: int
    project_id: int
    filename: str
    content_type: str | None
    size: int
    created_at: datetime

    model_config = {"from_attributes": True}
