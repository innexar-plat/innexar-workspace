"""Pydantic schemas for Hestia workspace API."""
from pydantic import BaseModel, Field


class HestiaUserCreate(BaseModel):
    """Create Hestia user (v-add-user). Optional first_name/last_name for display name."""

    user: str = Field(..., min_length=1, description="Username")
    password: str = Field(..., min_length=1, description="Password")
    email: str = Field(..., min_length=1, description="Email")
    package: str = Field(default="default", description="Package name")
    first_name: str = Field(default="", description="First/display name (arg5)")
    last_name: str = Field(default="", description="Last name (arg6)")


class HestiaDomainCreate(BaseModel):
    """Add web domain to Hestia user."""

    domain: str = Field(..., min_length=1, description="Domain name")
    ip: str = Field(default="", description="IP or empty for default")
    aliases: str = Field(default="www", description="Aliases (e.g. www)")


class HestiaOverviewResponse(BaseModel):
    """Hestia connection overview."""

    connected: bool = Field(..., description="Whether Hestia is reachable")
    total_users: int = Field(default=0, description="Number of users")
    error: str | None = Field(default=None, description="Error message if not connected")


