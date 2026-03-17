"""Workspace system routes: config/integrations, system/seed, setup-wizard."""
import json
import smtplib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.encryption import decrypt_value, encrypt_value, mask_value
from app.core.rbac import RequirePermission
from app.models.feature_flag import FeatureFlag
from app.models.hestia_settings import HestiaSettings
from app.modules.crm.models import Pipeline, PipelineStage
from app.models.integration_config import IntegrationConfig
from app.models.role import role_permissions, user_roles
from app.models.user import User
from app.modules.system.schemas import (
    HestiaSettingsResponse,
    HestiaSettingsUpdate,
    IntegrationConfigCreate,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
    IntegrationTestResponse,
    ResetAdminPasswordRequest,
    SetupWizardRequest,
    SetupWizardResponse,
)

router = APIRouter(tags=["workspace-system"])


def _config_to_response(c: IntegrationConfig) -> IntegrationConfigResponse:
    """Build response with value masked (never return decrypted)."""
    plain = decrypt_value(c.value_encrypted)
    return IntegrationConfigResponse(
        id=c.id,
        org_id=c.org_id,
        scope=c.scope,
        customer_id=c.customer_id,
        provider=c.provider,
        key=c.key,
        value_masked=mask_value(plain),
        mode=c.mode,
        enabled=c.enabled,
        last_tested_at=c.last_tested_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# ---------- Config / Integrations ----------
integrations_router = APIRouter(prefix="/config", tags=["workspace-config"])


@integrations_router.get("/integrations", response_model=list[IntegrationConfigResponse])
async def list_integrations(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:read"))],
):
    """List integration configs (value masked)."""
    r = await db.execute(select(IntegrationConfig).order_by(IntegrationConfig.id))
    return [_config_to_response(c) for c in r.scalars().all()]


@integrations_router.post("/integrations", response_model=IntegrationConfigResponse, status_code=201)
async def create_integration(
    body: IntegrationConfigCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("config:write"))],
):
    """Create integration config (value stored encrypted)."""
    encrypted = encrypt_value(body.value)
    if encrypted is None:
        raise HTTPException(status_code=500, detail="Encryption not available")
    c = IntegrationConfig(
        org_id=current.org_id or "innexar",
        scope=body.scope,
        customer_id=body.customer_id,
        provider=body.provider,
        key=body.key,
        value_encrypted=encrypted,
        mode=body.mode,
        enabled=body.enabled,
    )
    db.add(c)
    await db.flush()
    await log_audit(
        db,
        entity="integration_config",
        entity_id=str(c.id),
        action="create",
        actor_type="staff",
        actor_id=str(current.id),
        org_id=c.org_id or "innexar",
    )
    await db.refresh(c)
    return _config_to_response(c)


@integrations_router.patch("/integrations/{config_id}", response_model=IntegrationConfigResponse)
async def update_integration(
    config_id: int,
    body: IntegrationConfigUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("config:write"))],
):
    """Update integration config (value encrypted if provided)."""
    r = await db.execute(select(IntegrationConfig).where(IntegrationConfig.id == config_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Integration config not found")
    if body.value is not None:
        enc = encrypt_value(body.value)
        if enc is None:
            raise HTTPException(status_code=500, detail="Encryption not available")
        c.value_encrypted = enc
    if body.mode is not None:
        c.mode = body.mode
    if body.enabled is not None:
        c.enabled = body.enabled
    await db.flush()
    await log_audit(
        db,
        entity="integration_config",
        entity_id=str(c.id),
        action="update",
        actor_type="staff",
        actor_id=str(current.id),
        org_id=c.org_id or "innexar",
    )
    await db.refresh(c)
    return _config_to_response(c)


@integrations_router.post(
    "/integrations/{config_id}/test",
    response_model=IntegrationTestResponse,
    responses={404: {"description": "Integration config not found"}},
)
async def test_integration(
    config_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> IntegrationTestResponse:
    """Test an integration config (Stripe, SMTP, Mercado Pago); updates last_tested_at on success. See docs/API.md."""
    r = await db.execute(select(IntegrationConfig).where(IntegrationConfig.id == config_id))
    c = r.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Integration config not found")
    plain = decrypt_value(c.value_encrypted) if c.value_encrypted else None
    provider = (c.provider or "").lower()
    try:
        if provider == "stripe":
            if not plain:
                return IntegrationTestResponse(ok=False, error="No secret key configured")
            try:
                import stripe as stripe_lib
            except ImportError:
                return IntegrationTestResponse(ok=False, error="stripe package not installed")
            stripe_lib.api_key = plain
            stripe_lib.Balance.retrieve()
            c.last_tested_at = datetime.now(UTC)
            await db.flush()
            return IntegrationTestResponse(ok=True, message="Stripe connection OK")
        if provider == "smtp":
            if not plain:
                return IntegrationTestResponse(ok=False, error="No SMTP config configured")
            try:
                data = json.loads(plain)
            except (json.JSONDecodeError, TypeError):
                return IntegrationTestResponse(ok=False, error="Invalid SMTP config JSON")
            host = data.get("host") or "localhost"
            port = int(data.get("port") or 587)
            user = data.get("user") or ""
            password = data.get("password") or ""
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.starttls()
                if user and password:
                    server.login(user, password)
            c.last_tested_at = datetime.now(UTC)
            await db.flush()
            return IntegrationTestResponse(ok=True, message="SMTP connection OK")
        if provider == "mercadopago":
            return IntegrationTestResponse(ok=False, error="Test not implemented for Mercado Pago")
        if provider == "hestia":
            if not plain:
                return IntegrationTestResponse(ok=False, error="No Hestia config configured")
            try:
                data = json.loads(plain)
            except (json.JSONDecodeError, TypeError):
                return IntegrationTestResponse(ok=False, error="Invalid Hestia config JSON")
            from app.providers.hestia.client import HestiaClient
            base_url = (data.get("base_url") or "").rstrip("/")
            access_key = data.get("access_key") or ""
            secret_key = data.get("secret_key") or ""
            if not base_url or not access_key or not secret_key:
                return IntegrationTestResponse(ok=False, error="Missing base_url, access_key or secret_key")
            client = HestiaClient(base_url=base_url, access_key=access_key, secret_key=secret_key)
            client.request("v-list-users", returncode=True)
            c.last_tested_at = datetime.now(UTC)
            await db.flush()
            return IntegrationTestResponse(ok=True, message="Hestia connection OK")
        return IntegrationTestResponse(ok=False, error=f"Unknown provider: {provider}")
    except Exception as e:
        err_msg = str(e)
        if provider == "hestia" and ("522" in err_msg or "timed out" in err_msg.lower() or "connection" in err_msg.lower()):
            err_msg = (
                f"{err_msg}. Verifique: URL acessível a partir do backend (porta típica 8083), "
                "rede/firewall e Cloudflare (se aplicável). Veja docs/SETUP.md#erro-522-na-integração-hestia."
            )
        return IntegrationTestResponse(ok=False, error=err_msg)


# ---------- System / Seed ----------
seed_router = APIRouter(prefix="/system", tags=["workspace-system-seed"])


RBAC_PERMISSIONS = [
    "billing:read",
    "billing:write",
    "crm:read",
    "crm:write",
    "crm.leads.view",
    "crm.leads.create",
    "crm.leads.edit",
    "crm.deals.view",
    "crm.deals.edit",
    "crm.deals.move",
    "crm.pipeline.manage",
    "crm.reports.view",
    "projects:read",
    "projects:write",
    "support:read",
    "support:write",
    "config:read",
    "config:write",
    "dashboard:read",
]


async def _run_bootstrap(db: AsyncSession) -> tuple[bool, list[str]]:
    """Create default admin, RBAC, default feature flags. Returns (admin_created, flags_created)."""
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.role import Role

    admin_created = False
    r = await db.execute(select(User).where(User.email == "admin@innexar.com").limit(1))
    admin_user = r.scalar_one_or_none()
    if admin_user is None:
        admin_user = User(
            email="admin@innexar.com",
            password_hash=hash_password("change-me"),
            role="admin",
            org_id="innexar",
        )
        db.add(admin_user)
        await db.flush()
        admin_created = True

    perms: list[Permission] = []
    for slug in RBAC_PERMISSIONS:
        r = await db.execute(select(Permission).where(Permission.slug == slug).limit(1))
        p = r.scalar_one_or_none()
        if p is None:
            p = Permission(slug=slug, description=slug)
            db.add(p)
            await db.flush()
        perms.append(p)

    r = await db.execute(select(Role).where(Role.slug == "admin").limit(1))
    admin_role = r.scalar_one_or_none()
    if admin_role is None:
        admin_role = Role(org_id="innexar", name="Administrator", slug="admin")
        db.add(admin_role)
        await db.flush()
        for p in perms:
            await db.execute(insert(role_permissions).values(role_id=admin_role.id, permission_id=p.id))
        await db.execute(insert(user_roles).values(user_id=admin_user.id, role_id=admin_role.id))
    else:
        r = await db.execute(
            select(user_roles).where(
                and_(
                    user_roles.c.user_id == admin_user.id,
                    user_roles.c.role_id == admin_role.id,
                )
            ).limit(1)
        )
        if r.first() is None:
            await db.execute(insert(user_roles).values(user_id=admin_user.id, role_id=admin_role.id))
    await db.flush()

    flags_created: list[str] = []
    for key, enabled in [
        ("billing.enabled", True),
        ("portal.invoices.enabled", True),
        ("portal.tickets.enabled", True),
        ("portal.projects.enabled", True),
    ]:
        r = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key).limit(1))
        if r.scalar_one_or_none() is None:
            db.add(FeatureFlag(key=key, enabled=enabled))
            flags_created.append(key)
    await db.flush()

    # Default CRM pipeline (Vendas) if none exists
    r = await db.execute(select(Pipeline).where(Pipeline.org_id == "innexar").limit(1))
    if r.scalar_one_or_none() is None:
        pipeline = Pipeline(org_id="innexar", nome="Vendas")
        db.add(pipeline)
        await db.flush()
        for ordem, (nome, prob) in enumerate(
            [("Qualificação", 10), ("Proposta", 30), ("Negociação", 60), ("Fechado", 100)]
        ):
            db.add(
                PipelineStage(
                    pipeline_id=pipeline.id,
                    nome=nome,
                    ordem=ordem,
                    probabilidade=prob,
                )
            )
        await db.flush()

    return admin_created, flags_created


@seed_router.post("/seed", status_code=204)
async def seed(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """Bootstrap: create default admin, RBAC roles/permissions, feature flags. Protected by SEED_TOKEN or first-run."""
    if settings.SEED_TOKEN:
        if token != settings.SEED_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid seed token")
    else:
        r = await db.execute(select(User).limit(1))
        if r.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=403,
                detail="Seed only allowed when no users exist or with SEED_TOKEN",
            )
    await _run_bootstrap(db)


@seed_router.post("/reset-admin-password", status_code=204)
async def reset_admin_password(
    body: ResetAdminPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """Emergency: reset password for admin@innexar.com. Requires SEED_TOKEN. Use only when SEED_TOKEN is set."""
    if not settings.SEED_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="Reset admin password requires SEED_TOKEN to be configured",
        )
    if token != settings.SEED_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid seed token")
    from app.core.security import hash_password

    r = await db.execute(select(User).where(User.email == "admin@innexar.com").limit(1))
    admin_user = r.scalar_one_or_none()
    if admin_user is None:
        raise HTTPException(status_code=404, detail="Admin user not found; run seed first")
    admin_user.password_hash = hash_password(body.new_password)
    await db.commit()


@seed_router.post("/setup-wizard", response_model=SetupWizardResponse)
async def setup_wizard(
    body: SetupWizardRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = None,
):
    """One-shot setup: same as seed + optional IntegrationConfig (SMTP, Stripe, MP) and test_connection."""
    if settings.SEED_TOKEN:
        if token != settings.SEED_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid seed token")
    else:
        r = await db.execute(select(User).limit(1))
        if r.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=403,
                detail="Setup wizard only allowed when no users exist or with SEED_TOKEN",
            )

    admin_created, flags_created = await _run_bootstrap(db)
    org_id = "innexar"
    integrations_created: list[str] = []

    # Optional: create/update IntegrationConfig from body
    if body.smtp:
        val = json.dumps({
            "host": body.smtp.host,
            "port": body.smtp.port,
            "user": body.smtp.user,
            "password": body.smtp.password,
        })
        enc = encrypt_value(val)
        if enc:
            r = await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.provider == "smtp",
                    IntegrationConfig.key == "config",
                    IntegrationConfig.org_id == org_id,
                ).limit(1)
            )
            c = r.scalar_one_or_none()
            if c is None:
                c = IntegrationConfig(
                    org_id=org_id,
                    scope="global",
                    provider="smtp",
                    key="config",
                    value_encrypted=enc,
                    mode="test",
                    enabled=True,
                )
                db.add(c)
                await db.flush()
                integrations_created.append("smtp")
            else:
                c.value_encrypted = enc
                await db.flush()

    if body.stripe:
        enc = encrypt_value(body.stripe.secret_key)
        if enc:
            r = await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.provider == "stripe",
                    IntegrationConfig.key == "secret_key",
                    IntegrationConfig.org_id == org_id,
                ).limit(1)
            )
            c = r.scalar_one_or_none()
            if c is None:
                c = IntegrationConfig(
                    org_id=org_id,
                    scope="global",
                    provider="stripe",
                    key="secret_key",
                    value_encrypted=enc,
                    mode="test",
                    enabled=True,
                )
                db.add(c)
                await db.flush()
                integrations_created.append("stripe")
            else:
                c.value_encrypted = enc
                await db.flush()

    if body.mercadopago:
        enc = encrypt_value(body.mercadopago.access_token)
        if enc:
            r = await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.provider == "mercadopago",
                    IntegrationConfig.key == "access_token",
                    IntegrationConfig.org_id == org_id,
                ).limit(1)
            )
            c = r.scalar_one_or_none()
            if c is None:
                c = IntegrationConfig(
                    org_id=org_id,
                    scope="global",
                    provider="mercadopago",
                    key="access_token",
                    value_encrypted=enc,
                    mode="test",
                    enabled=True,
                )
                db.add(c)
                await db.flush()
                integrations_created.append("mercadopago")
            else:
                c.value_encrypted = enc
                await db.flush()

    if body.flags and body.flags.flags:
        for key, enabled in body.flags.flags.items():
            r = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key).limit(1))
            flag = r.scalar_one_or_none()
            if flag is None:
                db.add(FeatureFlag(key=key, enabled=enabled))
                flags_created.append(key)
            else:
                flag.enabled = enabled
        await db.flush()

    test_results: dict[str, str] | None = None
    if body.test_connection:
        test_results = {}
        r = await db.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.provider == "stripe",
                IntegrationConfig.org_id == org_id,
            ).limit(1)
        )
        stripe_cfg = r.scalar_one_or_none()
        if stripe_cfg and stripe_cfg.value_encrypted:
            try:
                import stripe as stripe_lib
                secret = decrypt_value(stripe_cfg.value_encrypted)
                if secret:
                    stripe_lib.api_key = secret
                    stripe_lib.Balance.retrieve()
                    stripe_cfg.last_tested_at = datetime.now(UTC)
                    test_results["stripe"] = "ok"
                else:
                    test_results["stripe"] = "error: no secret"
            except ImportError:
                test_results["stripe"] = "skipped"
            except Exception as e:
                test_results["stripe"] = f"error: {e!s}"
        if body.smtp:
            test_results["smtp"] = "skipped"

    return SetupWizardResponse(
        admin_created=admin_created,
        flags_created=flags_created,
        integrations_created=integrations_created,
        test_results=test_results,
    )


# ---------- Config / Hestia (dedicated area) ----------
hestia_config_router = APIRouter(prefix="/config/hestia", tags=["workspace-config-hestia"])


@hestia_config_router.get("/settings", response_model=HestiaSettingsResponse)
async def get_hestia_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("config:read"))],
) -> HestiaSettingsResponse:
    """Get Hestia provisioning settings (grace period, default package, auto-suspend). Creates default if missing."""
    org_id = current.org_id or "innexar"
    r = await db.execute(select(HestiaSettings).where(HestiaSettings.org_id == org_id).limit(1))
    row = r.scalar_one_or_none()
    if not row:
        row = HestiaSettings(org_id=org_id)
        db.add(row)
        await db.flush()
    return HestiaSettingsResponse(
        grace_period_days=row.grace_period_days,
        default_hestia_package=row.default_hestia_package,
        auto_suspend_enabled=row.auto_suspend_enabled,
    )


@hestia_config_router.put("/settings", response_model=HestiaSettingsResponse)
async def update_hestia_settings(
    body: HestiaSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(RequirePermission("config:write"))],
) -> HestiaSettingsResponse:
    """Update Hestia provisioning settings."""
    org_id = current.org_id or "innexar"
    r = await db.execute(select(HestiaSettings).where(HestiaSettings.org_id == org_id).limit(1))
    row = r.scalar_one_or_none()
    if not row:
        row = HestiaSettings(org_id=org_id)
        db.add(row)
        await db.flush()
    if body.grace_period_days is not None:
        row.grace_period_days = body.grace_period_days
    if body.default_hestia_package is not None:
        row.default_hestia_package = body.default_hestia_package
    if body.auto_suspend_enabled is not None:
        row.auto_suspend_enabled = body.auto_suspend_enabled
    await db.flush()
    await db.refresh(row)
    return HestiaSettingsResponse(
        grace_period_days=row.grace_period_days,
        default_hestia_package=row.default_hestia_package,
        auto_suspend_enabled=row.auto_suspend_enabled,
    )
