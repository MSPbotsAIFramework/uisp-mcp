import contextvars
from collections.abc import Callable
from typing import NamedTuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import UispClient
from .config import Settings


class _GatewayCreds(NamedTuple):
    api_key: str
    base_url: str


# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are
# isolated. Unlike every other connector in this org, UISP has no shared
# cloud endpoint to delegate to — each request's own X-Uisp-Base-Url names a
# different, independently reachable UISP instance, so there is no
# meaningful "shared" client state to pool here at all (see api_client.py).
_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "uisp_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> UispClient | None:
    """Resolve the active UispClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    return UispClient(creds.base_url, creds.api_key)


_REQUIRED_HEADERS = ["X-Uisp-Api-Key", "X-Uisp-Base-Url"]
_REQUIRED_HEADERS_MESSAGE = (
    "This server requires the X-Uisp-Api-Key header (a UISP API token, "
    "generated at Settings -> Users -> your user -> API tokens in the "
    "target UISP console) and the X-Uisp-Base-Url header (the full origin "
    "of that UISP instance, e.g. https://uisp.example.com)."
)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Uisp-Api-Key and X-Uisp-Base-Url (both required) from request
    headers and stores them in the contextvar. Returns 401 if either is
    missing on /mcp requests.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        api_key = request.headers.get("x-uisp-api-key")
        base_url = request.headers.get("x-uisp-base-url")

        if not api_key or not base_url:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": _REQUIRED_HEADERS_MESSAGE,
                    "required_headers": _REQUIRED_HEADERS,
                    "optional_headers": [],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set(_GatewayCreds(api_key, base_url))
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all UISP tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = FastMCP(
        name="uisp-mcp",
        instructions=(
            "UISP (Ubiquiti ISP) is Ubiquiti's ISP/MSP network management "
            "platform. This server reads site, device, outage, and "
            "event-log data from one UISP instance per request — the "
            "instance's own host and API token are supplied on every call "
            "via the X-Uisp-Base-Url and X-Uisp-Api-Key headers; there is "
            "no shared Ubiquiti cloud endpoint for UISP the way there is "
            "for UniFi. Typical flow: uisp_list_sites to find a site_id; "
            "uisp_list_devices (optionally filtered by that site_id) to "
            "find a device_id; uisp_get_site / uisp_get_device for detail "
            "on one; uisp_list_outages for recent/ongoing network outages; "
            "uisp_list_logs for the instance's event/audit log. "
            "Read-only: no device, firewall, or billing configuration "
            "action is exposed."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    client_factory: Callable[[], UispClient | None] = lambda: get_client_from_context(settings)

    from .tools import devices, logs, outages, sites

    sites.register(mcp, client_factory)
    devices.register(mcp, client_factory)
    outages.register(mcp, client_factory)
    logs.register(mcp, client_factory)

    return mcp
