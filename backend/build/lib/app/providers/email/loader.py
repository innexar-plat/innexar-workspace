"""Resolve email provider from IntegrationConfig or env."""
import json
import os
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.encryption import decrypt_value
from app.models.integration_config import IntegrationConfig
from app.providers.email.base import EmailProviderProtocol
from app.providers.email.smtp import SMTPProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_email_provider(
    db: "AsyncSession",
    org_id: str = "innexar",
) -> EmailProviderProtocol | None:
    """Resolve email provider from IntegrationConfig (tenant → global), else env fallback."""
    for scope in ["tenant", "global"]:
        q = select(IntegrationConfig).where(
            IntegrationConfig.provider == "smtp",
            IntegrationConfig.key == "config",
            IntegrationConfig.enabled.is_(True),
        )
        if scope == "tenant":
            q = q.where(
                IntegrationConfig.scope == "tenant",
                IntegrationConfig.org_id == org_id,
                IntegrationConfig.customer_id.is_(None),
            )
        else:
            q = q.where(
                IntegrationConfig.scope == "global",
                IntegrationConfig.customer_id.is_(None),
            )
        q = q.limit(1)
        r = await db.execute(q)
        cfg = r.scalar_one_or_none()
        if cfg and cfg.value_encrypted:
            plain = decrypt_value(cfg.value_encrypted)
            if plain:
                try:
                    data = json.loads(plain)
                    return SMTPProvider(
                        host=data.get("host"),
                        port=data.get("port"),
                        user=data.get("user"),
                        password=data.get("password"),
                    )
                except (json.JSONDecodeError, TypeError):
                    pass
    if os.environ.get("SMTP_HOST"):
        return SMTPProvider()
    return None
