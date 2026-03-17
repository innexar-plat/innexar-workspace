"""Resolve Cloudflare client from IntegrationConfig."""
import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.encryption import decrypt_value
from app.models.integration_config import IntegrationConfig
from app.providers.cloudflare.client import CloudflareClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_cloudflare_client(db: "AsyncSession", org_id: str = "innexar") -> CloudflareClient | None:
    """Load Cloudflare config from IntegrationConfig (tenant then global) and return client."""
    for scope in ("tenant", "global"):
        q = select(IntegrationConfig).where(
            IntegrationConfig.provider == "cloudflare",
            IntegrationConfig.key == "api_credentials",
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
                    api_token = (data.get("api_token") or "").strip()
                    account_id = (data.get("account_id") or "").strip() or None
                    if api_token:
                        return CloudflareClient(api_token=api_token, account_id=account_id)
                except (json.JSONDecodeError, TypeError):
                    pass
    return None
