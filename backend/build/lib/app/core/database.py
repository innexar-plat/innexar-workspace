"""Async database engine and session factory."""
from collections.abc import AsyncGenerator
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yield async session; commit on success, rollback on exception."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ensure_mp_subscription_schema() -> None:
    """Idempotent DDL: ensure billing_subscriptions.external_id and billing_mp_subscription_checkouts exist (for MP Assinaturas)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_billing_subscriptions_external_id "
                "ON billing_subscriptions (external_id)"
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS billing_mp_subscription_checkouts (
                    id SERIAL PRIMARY KEY,
                    invoice_id INTEGER NOT NULL REFERENCES billing_invoices(id),
                    mp_plan_id VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                )
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_billing_mp_subscription_checkouts_invoice_id "
                "ON billing_mp_subscription_checkouts (invoice_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_billing_mp_subscription_checkouts_mp_plan_id "
                "ON billing_mp_subscription_checkouts (mp_plan_id)"
            )
        )
    logger.info("MP subscription schema ensured")
