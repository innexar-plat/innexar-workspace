"""Application configuration via pydantic-settings."""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://workspace_user:change_me@localhost:5432/innexar_workspace"
    DATABASE_URL_TEST: str | None = None

    # Redis (optional)
    REDIS_URL: str = "redis://localhost:6379"

    # Auth - separate secrets for staff vs customer
    SECRET_KEY_STAFF: str = "change-me-in-production-staff-secret"
    SECRET_KEY_CUSTOMER: str = "change-me-in-production-customer-secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    FRONTEND_URL: str = "http://localhost:3000"
    # Public URL of this API (e.g. https://api.innexar.com.br). Used for session URLs in iframes (OpenClaw).
    # If unset, falls back to request.base_url (may be http behind proxy → Mixed Content).
    API_PUBLIC_URL: str | None = None
    # Base URL for portal (payment success/cancel redirects). If not set, falls back to FRONTEND_URL or portal.innexar.com.br
    PORTAL_URL: str | None = None

    # Encryption for integration secrets (Fernet key, base64)
    ENCRYPTION_KEY: str | None = None

    # Bootstrap token for POST /api/workspace/system/seed (optional)
    SEED_TOKEN: str | None = None

    # Storage (MinIO / S3-compatible)
    STORAGE_PROVIDER: str = "minio"
    MINIO_ENDPOINT: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_PROJECTS: str = "project-files"
    MINIO_SECURE: bool = False

    # OpenClaw: integração assistente IA (Control UI + WebChat + WhatsApp QR). Proxy encaminha path completo.
    # No Gateway configure gateway.controlUi.basePath = "/api/workspace/openclaw-ui" e allowedOrigins.
    OPENCLAW_GATEWAY_URL: str | None = None  # origem sem path, ex: http://openclaw:18789
    OPENCLAW_GATEWAY_WS_URL: str | None = None  # ex: ws://openclaw:18789/ws (se omitido, derivado de OPENCLAW_GATEWAY_URL)
    OPENCLAW_GATEWAY_HOST: str | None = None  # Host header para upstream (Coolify: FQDN do serviço, ex: xxx.sslip.io)
    OPENCLAW_PROXY_PATH: str = "openclaw-ui"  # path sob /api/workspace; deve coincidir com basePath no Gateway

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS string into list."""
        if isinstance(self.CORS_ORIGINS, str):
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        return list(self.CORS_ORIGINS)


settings = Settings()
