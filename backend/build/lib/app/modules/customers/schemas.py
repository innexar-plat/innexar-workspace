"""Pydantic schemas for workspace customers."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr


class CustomerCreate(BaseModel):
    """Body for creating a customer."""

    name: str
    email: EmailStr
    phone: str | None = None
    address: dict[str, Any] | None = None


class CustomerUpdate(BaseModel):
    """Body for updating a customer (partial)."""

    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: dict[str, Any] | None = None


class CustomerResponse(BaseModel):
    """Customer in list/detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: str
    name: str
    email: str
    phone: str | None
    address: dict[str, Any] | None
    created_at: datetime
    has_portal_access: bool = False


class SendCredentialsResponse(BaseModel):
    """Response after sending credentials."""

    ok: bool = True
    message: str = "Credentials sent by email"


class GeneratePasswordResponse(BaseModel):
    """Response with generated temporary password (admin only)."""

    password: str
    message: str = "Senha gerada. Use 'Enviar convite' para enviar por e-mail."
