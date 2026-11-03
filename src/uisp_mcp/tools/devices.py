"""Device inventory — read only.

GET /devices, GET /devices/{id}, per UISP's own Network API v2.1 spec. Not
yet exercised against a real live UISP instance — see README Known Gaps.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import UispClient, UispError
from ._common import NO_TOKEN, project_device, project_device_detail

_SITE_ID_DESC = "Optional: limit to devices belonging to this site UUID (from uisp_list_sites)."
_DEVICE_ID_DESC = "Device UUID, from uisp_list_devices."


def register(mcp: FastMCP, client_factory: Callable[[], UispClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_list_devices(
        site_id: Annotated[str | None, Field(description=_SITE_ID_DESC)] = None,
        role: Annotated[
            str | None, Field(description="Optional filter: device role, e.g. 'ap', 'router'.")
        ] = None,
        type: Annotated[
            str | None, Field(description="Optional filter: device type/category.")
        ] = None,
        authorized: Annotated[
            bool | None, Field(description="Optional filter: only authorized/unauthorized devices.")
        ] = None,
    ) -> str:
        """List devices, optionally scoped to one site.

        Use to find a device_id by site/role/type before calling
        uisp_get_device.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            # Fetched unfiltered and filtered here, same reasoning as
            # uisp_list_sites: the mirrored OpenAPI doc does not confirm
            # server-side filter support on GET /devices.
            result = await client.get("/devices")
            devices = result if isinstance(result, list) else result.get("data", [])
            projected = [project_device(d) for d in devices]
            if site_id is not None:
                projected = [d for d in projected if d["site_id"] == site_id]
            if role is not None:
                projected = [d for d in projected if d["role"] == role]
            if type is not None:
                projected = [d for d in projected if d["type"] == type]
            if authorized is not None:
                projected = [d for d in projected if d["authorized"] == authorized]
            return dump_json_capped({"devices": projected, "count": len(projected)})
        except UispError as e:
            return e.to_envelope()
        finally:
            await client.aclose()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_get_device(
        device_id: Annotated[str, Field(description=_DEVICE_ID_DESC)],
    ) -> str:
        """Get identification and current status overview for one device:
        status, uptime, CPU/RAM load, outage score, last-seen time."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/devices/{device_id}")
            return dump_json_capped(project_device_detail(result))
        except UispError as e:
            return e.to_envelope()
        finally:
            await client.aclose()
