"""Workspace Hestia management: users, domains, packages, overview."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rbac import RequirePermission
from app.models.user import User
from app.modules.hestia.schemas import (
    HestiaDomainCreate,
    HestiaOverviewResponse,
    HestiaUserCreate,
)
from app.providers.hestia.loader import get_hestia_client

router = APIRouter(prefix="/hestia", tags=["workspace-hestia"])


def _ensure_client(client):
    """Raise 503 if Hestia client is not configured."""
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Hestia not configured. Configure Hestia integration in Config → Integrations.",
        )


def _normalize_user_list(raw: list) -> list[dict]:
    """Convert list_users result to list of dicts with at least 'name'."""
    out = []
    for x in raw:
        if isinstance(x, dict):
            if "name" in x:
                out.append(x)
            else:
                out.append({"name": list(x.keys())[0] if x else "", **x})
        else:
            out.append({"name": str(x)})
    return out


@router.get("/overview", response_model=HestiaOverviewResponse)
async def hestia_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:read"))],
) -> HestiaOverviewResponse:
    """Hestia connection status and total users count."""
    client = await get_hestia_client(db)
    if client is None:
        return HestiaOverviewResponse(
            connected=False,
            total_users=0,
            error="Hestia not configured. Configure in Config → Integrations.",
        )
    try:
        users = client.list_users()
        total = len(users) if isinstance(users, list) else 0
        return HestiaOverviewResponse(connected=True, total_users=total)
    except Exception as e:
        err_msg = str(e)
        if "401" in err_msg or "Unauthorized" in err_msg:
            err_msg = (
                "Credenciais inválidas (401). No Hestia: User → API, confira se as chaves estão ativas e "
                "atualize a integração em Configurações → Integrações → Editar (Hestia) com access_key e secret_key corretos."
            )
        return HestiaOverviewResponse(
            connected=False,
            total_users=0,
            error=err_msg,
        )


@router.get("/users")
async def hestia_list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:read"))],
) -> list[dict]:
    """List Hestia users."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        raw = client.list_users()
        return _normalize_user_list(raw if isinstance(raw, list) else [])
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/users", status_code=201)
async def hestia_create_user(
    body: HestiaUserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Create Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.create_user(
            user=body.user,
            password=body.password,
            email=body.email,
            package=body.package,
            first_name=body.first_name,
            last_name=body.last_name,
        )
        return {"ok": True, "user": body.user}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/users/{user}/domains")
async def hestia_list_domains(
    user: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:read"))],
) -> list[dict]:
    """List web domains for a Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        return client.list_web_domains(user)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/users/{user}/domains", status_code=201)
async def hestia_add_domain(
    user: str,
    body: HestiaDomainCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Add web domain to Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.add_web_domain(
            user=user,
            domain=body.domain,
            ip=body.ip or "",
            aliases=body.aliases,
        )
        return {"ok": True, "user": user, "domain": body.domain}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/packages")
async def hestia_list_packages(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:read"))],
) -> list[dict]:
    """List Hestia packages."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        return client.list_packages()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.delete("/users/{user}")
async def hestia_delete_user(
    user: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Delete Hestia user (and associated data)."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.delete_user(user)
        return {"ok": True, "user": user}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/users/{user}/domains/{domain}")
async def hestia_delete_domain(
    user: str,
    domain: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Delete web domain from Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.delete_web_domain(user=user, domain=domain)
        return {"ok": True, "user": user, "domain": domain}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/users/{user}/suspend")
async def hestia_suspend_user(
    user: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Suspend Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.suspend_user(user)
        return {"ok": True, "user": user, "suspended": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/users/{user}/unsuspend")
async def hestia_unsuspend_user(
    user: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(RequirePermission("config:write"))],
) -> dict:
    """Unsuspend Hestia user."""
    client = await get_hestia_client(db)
    _ensure_client(client)
    try:
        client.unsuspend_user(user)
        return {"ok": True, "user": user, "suspended": False}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
