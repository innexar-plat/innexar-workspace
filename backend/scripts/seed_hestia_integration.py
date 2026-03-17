"""
Seed Hestia integration config in the database.

Usage (from backend directory):
  export HESTIA_BASE_URL="https://seu-hestia.exemplo.com:8083"
  export HESTIA_ACCESS_KEY="seu_access_key"
  export HESTIA_SECRET_KEY="seu_secret_key"
  python -m scripts.seed_hestia_integration

Or with .env loaded (e.g. by your shell):
  python -m scripts.seed_hestia_integration

Required from HestiaCP:
  - base_url: panel URL (e.g. https://panel.seudominio.com:8083)
  - access_key + secret_key: User -> API in HestiaCP
"""
import asyncio
import json
import os
import sys

_backend_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
_build_lib = os.path.join(_backend_dir, "build", "lib")
if os.path.isdir(_build_lib):
    sys.path.insert(0, _build_lib)
else:
    sys.path.insert(0, _backend_dir)

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.encryption import encrypt_value  # noqa: E402
from app.models.integration_config import IntegrationConfig  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def main() -> None:
    base_url = (os.environ.get("HESTIA_BASE_URL") or "").strip().rstrip("/")
    access_key = (os.environ.get("HESTIA_ACCESS_KEY") or "").strip()
    secret_key = (os.environ.get("HESTIA_SECRET_KEY") or "").strip()

    if not base_url or not access_key or not secret_key:
        print("Missing env. Set HESTIA_BASE_URL, HESTIA_ACCESS_KEY, HESTIA_SECRET_KEY")
        print(
            "Example: HESTIA_BASE_URL=https://panel.example.com:8083 "
            "HESTIA_ACCESS_KEY=... HESTIA_SECRET_KEY=..."
        )
        sys.exit(1)

    value = json.dumps(
        {"base_url": base_url, "access_key": access_key, "secret_key": secret_key}
    )
    encrypted = encrypt_value(value)

    if not encrypted:
        print("Encryption failed. Check ENCRYPTION_KEY or SECRET_KEY_STAFF.")
        sys.exit(1)

    org_id = os.environ.get("INTEGRATION_ORG_ID", "innexar")
    scope = "tenant"

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(IntegrationConfig)
            .where(IntegrationConfig.provider == "hestia")
            .where(IntegrationConfig.key == "api_credentials")
            .where(IntegrationConfig.org_id == org_id)
            .where(IntegrationConfig.scope == scope)
            .limit(1)
        )
        existing = r.scalar_one_or_none()

        if existing:
            existing.value_encrypted = encrypted
            existing.enabled = True
            print("Hestia integration updated (id=%s)." % existing.id)
        else:
            c = IntegrationConfig(
                org_id=org_id,
                scope=scope,
                customer_id=None,
                provider="hestia",
                key="api_credentials",
                value_encrypted=encrypted,
                mode=os.environ.get("HESTIA_MODE", "live"),
                enabled=True,
            )
            db.add(c)
            await db.flush()
            print("Hestia integration created (id=%s)." % c.id)

        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
