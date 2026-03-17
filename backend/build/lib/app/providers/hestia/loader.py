"""Resolve Hestia client from IntegrationConfig."""
import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.encryption import decrypt_value
from app.models.integration_config import IntegrationConfig
from app.providers.hestia.client import HestiaClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_hestia_client(db: "AsyncSession", org_id: str = "innexar") -> HestiaClient | None:
    """Load Hestia config from IntegrationConfig (tenant/workspace then global) and return client."""
    for scope in ("tenant", "workspace", "global"):
        q = select(IntegrationConfig).where(
            IntegrationConfig.provider == "hestia",
            IntegrationConfig.key == "api_credentials",
            IntegrationConfig.enabled.is_(True),
        )
        if scope in ("tenant", "workspace"):
            q = q.where(
                IntegrationConfig.scope == scope,
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
                    base_url = data.get("base_url") or ""
                    access_key = data.get("access_key") or ""
                    secret_key = data.get("secret_key") or ""
                    if base_url and access_key and secret_key:
                        return HestiaClient(base_url=base_url, access_key=access_key, secret_key=secret_key)
                except (json.JSONDecodeError, TypeError):
                    pass
    return None
