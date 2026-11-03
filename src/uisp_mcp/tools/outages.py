"""Network outages — read only.

GET /outages, per UISP's own Network API v2.1 spec
(``summary: "List of all network outages for last month"``). The upstream
endpoint's required page/count pagination is looped internally (see
``_common.paginate_all``) — the agent passes a simple ``max_items`` cap
instead of raw page numbers. Not yet exercised against a real live UISP
instance — see README Known Gaps.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import UispClient, UispError
from ._common import NO_TOKEN, paginate_all, project_outage

_MAX_ITEMS_DESC = (
    "Internal upstream-pagination cap on how many raw outage records to "
    "scan before stopping (default 200). Not a result-count limit — the "
    "returned list may be smaller once device_id/in_progress filters are "
    "applied. Raise it to scan further back; the tool reports truncated=true "
    "if more may exist beyond the cap."
)


def register(mcp: FastMCP, client_factory: Callable[[], UispClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_list_outages(
        device_id: Annotated[
            str | None, Field(description="Optional filter: only outages for this device UUID.")
        ] = None,
        in_progress: Annotated[
            bool | None,
            Field(description="Optional filter: true for only currently-ongoing outages."),
        ] = None,
        max_items: Annotated[int, Field(description=_MAX_ITEMS_DESC)] = 200,
    ) -> str:
        """List recent/ongoing network outages this UISP instance retains.

        Covers both full outages and unreachable events, each tied to a
        device and site. Use uisp_list_devices / uisp_list_sites first if
        you need a device_id/site_id to cross-reference the result.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            scanned, truncated = await paginate_all(client, "/outages", max_items)
            outages = [project_outage(o) for o in scanned]
            if device_id is not None:
                outages = [o for o in outages if o["device_id"] == device_id]
            if in_progress is not None:
                outages = [o for o in outages if o["ongoing"] == in_progress]
            return dump_json_capped(
                {"outages": outages, "scanned": len(scanned), "truncated": truncated}
            )
        except UispError as e:
            return e.to_envelope()
