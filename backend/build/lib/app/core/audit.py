"""Audit log: log_audit() for critical changes."""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_audit(
    db: AsyncSession,
    *,
    entity: str,
    entity_id: str | None = None,
    action: str,
    actor_type: str,
    actor_id: str | None = None,
    org_id: str = "innexar",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append an audit log entry."""
    entry = AuditLog(
        org_id=org_id,
        entity=entity,
        entity_id=entity_id,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload,
    )
    db.add(entry)
    await db.flush()
