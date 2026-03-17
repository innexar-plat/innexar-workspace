"""System schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class IntegrationConfigCreate(BaseModel):
    """Create integration config."""

    scope: str
    customer_id: int | None = None
    provider: str
    key: str
    value: str
    mode: str = "test"
    enabled: bool = True


class IntegrationConfigUpdate(BaseModel):
    """Update integration config (partial)."""

    value: str | None = None
    mode: str | None = None
    enabled: bool | None = None


class IntegrationTestResponse(BaseModel):
    """Result of testing an integration config (POST /api/workspace/config/integrations/{id}/test)."""

    ok: bool
    message: str | None = None  # Present when ok is True (e.g. 'Stripe connection OK')
    error: str | None = None  # Present when ok is False (e.g. connection error message)


class IntegrationConfigResponse(BaseModel):
    """Integration config response (value masked)."""

    id: int
    org_id: str
    scope: str
    customer_id: int | None
    provider: str
    key: str
    value_masked: str
    mode: str
    enabled: bool
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": False}


# ---------- Setup wizard ----------
class SetupWizardSmtp(BaseModel):
    """SMTP integration step."""

    host: str
    port: int = 587
    user: str = ""
    password: str = ""


class SetupWizardStripe(BaseModel):
    """Stripe integration step."""

    secret_key: str


class SetupWizardMercadoPago(BaseModel):
    """MercadoPago integration step."""

    access_token: str


class SetupWizardFlags(BaseModel):
    """Feature flags step (key -> enabled)."""

    flags: dict[str, bool] = {}


class SetupWizardRequest(BaseModel):
    """Setup wizard body: optional steps and test_connection."""

    smtp: SetupWizardSmtp | None = None
    stripe: SetupWizardStripe | None = None
    mercadopago: SetupWizardMercadoPago | None = None
    flags: SetupWizardFlags | None = None
    test_connection: bool = False


class SetupWizardResponse(BaseModel):
    """Setup wizard result summary."""

    admin_created: bool
    flags_created: list[str]
    integrations_created: list[str]
    test_results: dict[str, Any] | None = None


# ---------- Hestia settings (workspace config) ----------
class HestiaSettingsResponse(BaseModel):
    """Hestia provisioning settings (grace period, package, auto-suspend)."""

    grace_period_days: int
    default_hestia_package: str | None
    auto_suspend_enabled: bool


class HestiaSettingsUpdate(BaseModel):
    """Update Hestia settings (partial)."""

    grace_period_days: int | None = None
    default_hestia_package: str | None = None
    auto_suspend_enabled: bool | None = None


class ResetAdminPasswordRequest(BaseModel):
    """Body for POST /api/workspace/system/reset-admin-password. Emergency only, requires SEED_TOKEN."""

    new_password: str = "change-me"
