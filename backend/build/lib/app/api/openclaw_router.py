"""OpenClaw integration: session token + reverse proxy for Control UI (WhatsApp QR, WebChat, etc.).

Backend env:
  OPENCLAW_GATEWAY_URL: origem do Gateway sem path (ex: http://openclaw:18789).
  OPENCLAW_GATEWAY_WS_URL: (opcional) WebSocket do Gateway (ex: ws://openclaw:18789/ws).
  OPENCLAW_PROXY_PATH: path do proxy sob /api/workspace (default: openclaw-ui).

OpenClaw Gateway (opcional.json / openclaw.json) para integração total:
  gateway.controlUi.basePath = "/api/workspace/openclaw-ui"
  gateway.controlUi.allowedOrigins = ["https://seu-frontend", "https://sua-api"]
Assim a UI usa este path para assets e WebSocket; o proxy encaminha o path completo.
"""
import asyncio
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response

from app.core.auth_staff import get_current_staff
from app.core.config import settings
from app.core.security import create_openclaw_proxy_token, decode_openclaw_proxy_token
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

OPENCLAW_COOKIE = "openclaw_proxy"
OPENCLAW_COOKIE_MAX_AGE = 600  # 10 min
PROXY_PATH = "openclaw-ui"

# Allow embedding in iframe from app/portal (required for Control UI).
# Overrides X-Frame-Options from reverse proxy; CSP frame-ancestors takes precedence.
FRAME_ANCESTORS = "frame-ancestors 'self' https://app.innexar.com.br https://portal.innexar.com.br https://api.innexar.com.br"


def _gateway_url() -> str | None:
    """Base URL of OpenClaw Gateway (e.g. http://openclaw:18789 or http://host:18789/openclaw)."""
    url = getattr(settings, "OPENCLAW_GATEWAY_URL", None) or settings.OPENCLAW_GATEWAY_URL
    if not url:
        return None
    return url.rstrip("/")


def _gateway_ws_url() -> str | None:
    """WebSocket URL for OpenClaw Gateway. Default: derive from OPENCLAW_GATEWAY_URL (http -> ws, add /ws)."""
    ws = getattr(settings, "OPENCLAW_GATEWAY_WS_URL", None) or settings.OPENCLAW_GATEWAY_WS_URL
    if ws:
        return ws.rstrip("/")
    base = _gateway_url()
    if not base:
        return None
    if base.startswith("https://"):
        return base.replace("https://", "wss://", 1) + "/ws"
    return base.replace("http://", "ws://", 1) + "/ws"


def _validate_token(token: str) -> bool:
    return decode_openclaw_proxy_token(token) is not None


@router.get("/openclaw-session")
async def openclaw_session(
    current_user: Annotated[User, Depends(get_current_staff)],
    request: Request,
) -> dict[str, str | int | bool]:
    """
    Workspace: retorna URL para carregar a interface OpenClaw (Control UI + WebChat).
    Requer auth staff. O frontend exibe essa URL em um iframe (WhatsApp QR, canais, etc.).
    Configure OPENCLAW_GATEWAY_URL no backend para ativar.
    """
    gateway = _gateway_url()
    if not gateway:
        return {
            "enabled": False,
            "url": "",
            "message": "OpenClaw não configurado. Defina OPENCLAW_GATEWAY_URL.",
        }
    token = create_openclaw_proxy_token(expires_minutes=10)
    base = settings.API_PUBLIC_URL
    if not base:
        base = str(request.base_url).rstrip("/")
    else:
        base = base.rstrip("/")
    url = f"{base}/api/workspace/{PROXY_PATH}?t={token}"
    return {
        "enabled": True,
        "url": url,
        "expires_in": 600,
    }


def _iframe_headers() -> dict[str, str]:
    """Headers to allow embedding in iframe (app.innexar.com.br)."""
    return {"Content-Security-Policy": FRAME_ANCESTORS}


async def _proxy_request(
    request: Request,
    token: str | None,
    cookie: str | None,
) -> Response:
    """Validate token (from query or cookie) and proxy to OpenClaw Gateway (full path)."""
    if not token and not cookie:
        return Response(
            status_code=401,
            content="Missing token (?t=) or session cookie",
            headers=_iframe_headers(),
        )
    if token and not _validate_token(token):
        return Response(
            status_code=403,
            content="Invalid or expired token",
            headers=_iframe_headers(),
        )
    if cookie and not _validate_token(cookie):
        return Response(
            status_code=403,
            content="Invalid or expired session",
            headers=_iframe_headers(),
        )
    gateway = _gateway_url()
    if not gateway:
        return Response(
            status_code=503,
            content="OpenClaw gateway not configured",
            headers=_iframe_headers(),
        )
    # Forward full path so Gateway with basePath /api/workspace/openclaw-ui receives same path
    path = request.url.path
    upstream = f"{gateway}{path}" if path.startswith("/") else f"{gateway}/{path}"
    try:
        # connect=15s: gateway may be cold-starting; read=60s for long responses
        timeout = httpx.Timeout(connect=15.0, read=60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "cookie", "authorization")}
            r = await client.request(
                request.method,
                upstream,
                params=request.query_params,
                headers=headers,
                content=await request.body(),
            )
            out_headers = {k: v for k, v in r.headers.items() if k.lower() not in ("transfer-encoding", "content-encoding")}
            out_headers.update(_iframe_headers())
            return Response(status_code=r.status_code, content=r.content, headers=out_headers)
    except httpx.ConnectError as e:
        logger.warning("OpenClaw gateway unreachable: %s", e)
        return Response(
            status_code=503,
            content="OpenClaw Gateway indisponível. Verifique OPENCLAW_GATEWAY_URL e se o serviço está no ar.",
            headers=_iframe_headers(),
        )
    except Exception as e:
        logger.exception("OpenClaw proxy error: %s", e)
        return Response(
            status_code=502,
            content="Erro ao comunicar com o Gateway",
            headers=_iframe_headers(),
        )


@router.get(f"/{PROXY_PATH}")
@router.get(f"/{PROXY_PATH}/{{full_path:path}}")
async def openclaw_proxy(
    request: Request,
    full_path: str = "",
    t: str | None = Query(None, alias="t"),
) -> Response:
    """
    Proxy para a interface OpenClaw. Requer token em ?t= (retornado por /openclaw-session)
    ou cookie de sessão. Na primeira carga com ?t=, define cookie e redireciona para remover t da URL.
    """
    cookie = request.cookies.get(OPENCLAW_COOKIE)

    # First load with token: set cookie and redirect to same URL without t
    if t and _validate_token(t):
        redirect_to = request.url.path
        if request.query_params:
            qs = [f"{k}={v}" for k, v in request.query_params.items() if k != "t"]
            if qs:
                redirect_to = f"{redirect_to}?{'&'.join(qs)}"
        response = RedirectResponse(url=redirect_to, status_code=302)
        response.set_cookie(
            OPENCLAW_COOKIE,
            t,
            max_age=OPENCLAW_COOKIE_MAX_AGE,
            path="/api/workspace",
            httponly=True,
            samesite="lax",
        )
        return response

    return await _proxy_request(request, t, cookie)


def _ws_token_from_scope(scope: dict) -> str | None:
    """Extract t= token from WebSocket scope query_string."""
    qs = (scope.get("query_string") or b"").decode()
    for part in qs.split("&"):
        if part.startswith("t="):
            return part[2:].strip()
    return None


@router.websocket(f"/{PROXY_PATH}/ws")
async def openclaw_proxy_ws(websocket: WebSocket) -> None:
    """Proxy WebSocket para o OpenClaw Gateway (Control UI). Requer cookie ou ?t=."""
    t = _ws_token_from_scope(websocket.scope)
    cookie = websocket.cookies.get(OPENCLAW_COOKIE)
    if not t and not cookie:
        await websocket.close(1008)
        return
    if t and not _validate_token(t):
        await websocket.close(1008)
        return
    if cookie and not _validate_token(cookie):
        await websocket.close(1008)
        return
    ws_url = _gateway_ws_url()
    if not ws_url:
        await websocket.close(1011)
        return
    try:
        import websockets
    except ImportError:
        logger.warning("websockets package not installed; OpenClaw WS proxy disabled")
        await websocket.close(1011)
        return
    await websocket.accept()
    try:
        async with websockets.connect(ws_url) as upstream:
            async def from_client() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def from_upstream() -> None:
                try:
                    while True:
                        message = await upstream.recv()
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            await asyncio.gather(from_client(), from_upstream())
    except Exception as e:
        logger.warning("OpenClaw WS proxy upstream error: %s", e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
