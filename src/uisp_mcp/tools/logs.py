"""Event/audit log — read only.

GET /logs, per UISP's own Network API v2.1 spec
(``summary: "List of all log items."``). The upstream endpoint's required
page/count pagination is looped internally (see ``_common.paginate_all``),
same technique as uisp_list_outages. Not yet exercised against a real live
UISP instance — see README Known Gaps.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import UispClient, UispError
from ._common import NO_TOKEN, paginate_all, project_log

_MAX_ITEMS_DESC = (
    "Internal upstream-pagination cap on how many raw log entries to scan "
    "before stopping (default 200). Not a result-count limit — the "
    "returned list may be smaller once site_id/device_id/level filters are "
    "applied. The tool reports truncated=true if more may exist beyond the "
    "cap."
)


def register(mcp: FastMCP, client_factory: Callable[[], UispClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def uisp_list_logs(
        site_id: Annotated[
            str | None, Field(description="Optional filter: only entries for this site UUID.")
        ] = None,
        device_id: Annotated[
            str | None, Field(description="Optional filter: only entries for this device UUID.")
        ] = None,
        level: Annotated[
            str | None, Field(description="Optional filter: 'info', 'warning', or 'error'.")
        ] = None,
        max_items: Annotated[int, Field(description=_MAX_ITEMS_DESC)] = 200,
    ) -> str:
        """List recent event/audit log entries from this UISP instance."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            scanned, truncated = await paginate_all(client, "/logs", max_items)
            logs = [project_log(entry) for entry in scanned]
            if site_id is not None:
                logs = [entry for entry in logs if entry["site_id"] == site_id]
            if device_id is not None:
                logs = [entry for entry in logs if entry["device_id"] == device_id]
            if level is not None:
                logs = [entry for entry in logs if entry["level"] == level]
            return dump_json_capped(
                {"logs": logs, "scanned": len(scanned), "truncated": truncated}
            )
        except UispError as e:
            return e.to_envelope()
