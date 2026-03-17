"""Auth request/response schemas."""
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """Login body (staff or customer)."""

    email: EmailStr
    password: str


class StaffLoginResponse(BaseModel):
    """Staff login response."""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


class CustomerLoginResponse(BaseModel):
    """Customer login response."""

    access_token: str
    token_type: str = "bearer"
    customer_user_id: int
    customer_id: int
    email: str


class StaffMeResponse(BaseModel):
    """GET /api/workspace/me response."""

    id: int
    email: str
    role: str
    org_id: str


class CustomerMeResponse(BaseModel):
    """GET /api/portal/me response."""

    id: int
    email: str
    customer_id: int
    email_verified: bool


class ForgotPasswordRequest(BaseModel):
    """Staff or customer: request password reset (email only)."""

    email: EmailStr


class StaffResetPasswordRequest(BaseModel):
    """Staff: set new password using token from email."""

    token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    """Change password while authenticated (current + new)."""

    current_password: str
    new_password: str
