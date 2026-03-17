"""Innexar Workspace API - 3-layer backend."""
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.openclaw_router import router as openclaw_router
from app.api.portal_router import router as portal_router
from app.api.public_router import router as public_router
from app.api.workspace_router import router as workspace_router
from app.modules.billing.router_portal import router as billing_portal_router
from app.modules.billing.router_public import router as billing_public_router
from app.modules.checkout.router_public import router as checkout_public_router
from app.modules.products.router_public import router as products_public_router
from app.modules.billing.router_workspace import router as billing_workspace_router
from app.modules.crm.router_workspace import router as crm_workspace_router
from app.modules.customers.router_workspace import router as customers_workspace_router
from app.modules.files.router_portal import router as files_portal_router
from app.modules.files.router_workspace import router as files_workspace_router
from app.modules.projects.router_portal import router as projects_portal_router
from app.modules.projects.router_workspace import router as projects_workspace_router
from app.modules.support.router_portal import router as support_portal_router
from app.modules.support.router_workspace import router as support_workspace_router
from app.modules.dashboard.router_workspace import router as dashboard_workspace_router
from app.modules.notifications.router_portal import router as notifications_portal_router
from app.modules.orders.router_workspace import router as orders_workspace_router
from app.modules.hestia.router_workspace import router as hestia_workspace_router
from app.modules.system.router_workspace import (
    hestia_config_router as system_hestia_config_router,
    integrations_router as system_integrations_router,
    seed_router as system_seed_router,
)
from app.core.config import settings
from app.core.database import Base, engine, ensure_mp_subscription_schema

# Import models so Base.metadata knows all tables
from app.models import (  # noqa: F401
    AuditLog,
    Customer,
    CustomerUser,
    FeatureFlag,
    HestiaSettings,
    IntegrationConfig,
    Notification,
    Permission,
    ProjectRequest,
    Role,
    StaffPasswordResetToken,
    User,
)
from app.modules.billing.models import (  # noqa: F401
    Invoice,
    MPSubscriptionCheckout,
    PaymentAttempt,
    PricePlan,
    Product,
    ProvisioningRecord,
    Subscription,
    WebhookEvent,
)
from app.modules.crm.models import Contact  # noqa: F401
from app.modules.files.models import ProjectFile  # noqa: F401
from app.modules.projects.models import Project  # noqa: F401
from app.modules.support.models import Ticket, TicketMessage  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup (for MVP; use Alembic in production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_mp_subscription_schema()
    yield
    await engine.dispose()


app = FastAPI(
    title="Innexar Workspace API",
    description="API em 3 camadas: workspace (staff), portal (cliente), public (público). "
    "Documentação interativa: /docs (Swagger UI) e /redoc (ReDoc). Contrato e comportamento: docs/API.md.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"^https?://(.*\.)?innexar\.com\.br$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

_CORS_ORIGIN_REGEX = re.compile(r"^https?://(.*\.)?innexar\.com\.br$")


def _cors_headers_for_request(request: Request) -> dict[str, str]:
    """Return CORS headers for error responses so the client can read the body (same rules as CORSMiddleware)."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    if origin in settings.cors_origins_list or _CORS_ORIGIN_REGEX.fullmatch(origin):
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ensure unhandled exceptions return JSON with CORS headers so the client can read the error."""
    headers = _cors_headers_for_request(request)
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )
    logging.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )


app.include_router(workspace_router, prefix="/api/workspace", tags=["workspace"])
app.include_router(openclaw_router, prefix="/api/workspace", tags=["workspace-openclaw"])
app.include_router(billing_workspace_router, prefix="/api/workspace")
app.include_router(crm_workspace_router, prefix="/api/workspace")
app.include_router(customers_workspace_router, prefix="/api/workspace")
app.include_router(projects_workspace_router, prefix="/api/workspace")
app.include_router(files_workspace_router, prefix="/api/workspace")
app.include_router(support_workspace_router, prefix="/api/workspace")
app.include_router(dashboard_workspace_router, prefix="/api/workspace")
app.include_router(orders_workspace_router, prefix="/api/workspace")
app.include_router(system_integrations_router, prefix="/api/workspace")
app.include_router(system_hestia_config_router, prefix="/api/workspace")
app.include_router(hestia_workspace_router, prefix="/api/workspace")
app.include_router(system_seed_router, prefix="/api/workspace")
app.include_router(portal_router, prefix="/api/portal", tags=["portal"])
app.include_router(billing_portal_router, prefix="/api/portal")
app.include_router(projects_portal_router, prefix="/api/portal")
app.include_router(files_portal_router, prefix="/api/portal")
app.include_router(support_portal_router, prefix="/api/portal")
app.include_router(notifications_portal_router, prefix="/api/portal")
app.include_router(public_router, prefix="/api/public", tags=["public"])
app.include_router(checkout_public_router, prefix="/api/public")
app.include_router(products_public_router, prefix="/api/public")
app.include_router(billing_public_router, prefix="/api/public")


@app.get(
    "/health",
    responses={
        200: {"description": "OK", "content": {"application/json": {"example": {"status": "ok", "database": "ok"}}}},
        503: {"description": "Database unreachable", "content": {"application/json": {"example": {"detail": "database unreachable"}}}},
    },
)
async def health() -> dict[str, str]:
    """Public health check; verifies DB connectivity. Returns 503 if DB unreachable. See docs/API.md."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail="database unreachable") from e


@app.get("/")
async def root() -> dict[str, str]:
    """Root."""
    return {"message": "Innexar Workspace API", "version": "0.1.0"}
