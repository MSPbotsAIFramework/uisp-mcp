"""Site discovery — read only.

GET /sites, GET /sites/{id}, per UISP's own Network API v2.1 spec. Not yet
exercised against a real live UISP instance — see README Known Gaps.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import UispClient, UispError
from ._common import NO_TOKEN, project_site, project_site_detail

_SITE_TYPE_DESC = "Optional filter: 'site' or 'endpoint'. Omit to return both."
_SITE_ID_DESC = "Site UUID, from uisp_list_sites."


def register(mcp: FastMCP, client_factory: Callable[[], UispClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_list_sites(
        site_type: Annotated[str | None, Field(description=_SITE_TYPE_DESC)] = None,
        ip: Annotated[
            str | None, Field(description="Optional filter: exact device/site IP address.")
        ] = None,
    ) -> str:
        """List every site in this UISP instance.

        A "site" is a physical location grouping devices. Call this first
        if you don't already have a site_id — almost every other tool here
        can be scoped to one.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            # Fetched unfiltered and filtered here rather than passed
            # upstream as query params: the mirrored OpenAPI doc does not
            # confirm server-side filter support on GET /sites, so this
            # server applies both filters itself after fetching the full
            # list (same technique as uisp_list_outages/uisp_list_logs).
            result = await client.get("/sites")
            sites = result if isinstance(result, list) else result.get("data", [])
            if ip is not None:
                sites = [
                    s
                    for s in sites
                    if s.get("ip") == ip or s.get("identification", {}).get("ip") == ip
                ]
            projected = [project_site(s) for s in sites]
            if site_type is not None:
                projected = [s for s in projected if s["type"] == site_type]
            return dump_json_capped({"sites": projected, "count": len(projected)})
        except UispError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_get_site(
        site_id: Annotated[str, Field(description=_SITE_ID_DESC)],
    ) -> str:
        """Get full detail for one site by its UUID."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/sites/{site_id}")
            return dump_json_capped(project_site_detail(result))
        except UispError as e:
            return e.to_envelope()
