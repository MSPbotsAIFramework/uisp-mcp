from typing import Any

from ..api_client import UispClient

from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No UISP credentials. Send the X-Uisp-Api-Key and X-Uisp-Base-Url headers.",
    False,
)


def project_site(site: dict) -> dict:
    """Trim a raw UISP Site object down to the fields this server's list
    view needs (SOP token-economy: the full Site schema also carries QoS
    config, UCRM billing linkage, and deprecated AirMax/AirFiber blocks that
    this ticket's scope does not ask for — see
    docs/tickets/PRD-21265.md -> Decisions made by DEV01).
    """
    return {
        "id": site.get("id"),
        "name": site.get("name"),
        "type": site.get("identification", {}).get("type") or site.get("type"),
        "status": site.get("status", {}).get("overall") or site.get("status"),
        "suspended": site.get("suspended", False),
    }


def project_site_detail(site: dict) -> dict:
    """Fuller projection for uisp_get_site: the caller already picked one
    specific site and is more likely to want detail than the list view
    needs."""
    detail = project_site(site)
    detail["updated"] = site.get("updated")
    return detail


def project_device(device: dict) -> dict:
    """Trim a raw UISP DeviceStatusOverview object down to the fields this
    server's list view needs (same token-economy reasoning as
    project_site)."""
    identification = device.get("identification", {})
    overview = device.get("overview", {})
    return {
        "id": device.get("id") or identification.get("id"),
        "name": identification.get("name") or device.get("name"),
        "model": identification.get("model") or device.get("model"),
        "role": identification.get("role") or device.get("role"),
        "type": identification.get("type") or device.get("type"),
        "status": overview.get("status") or device.get("status"),
        "site_id": device.get("identification", {}).get("site", {}).get("id")
        or device.get("site_id"),
        "authorized": identification.get("authorized", device.get("authorized", True)),
    }


def project_device_detail(device: dict) -> dict:
    """Fuller projection for uisp_get_device: identification plus the
    current overview (uptime/CPU/RAM/outage score/last-seen) the ticket's
    AC4 asks for."""
    detail = project_device(device)
    overview = device.get("overview", {})
    detail["overview"] = {
        "uptime": overview.get("uptime"),
        "cpu": overview.get("cpu"),
        "ram": overview.get("ram"),
        "outageScore": overview.get("outageScore"),
        "lastSeen": overview.get("lastSeen"),
    }
    return detail


def project_outage(outage: dict) -> dict:
    start = outage.get("start") or outage.get("startTimestamp")
    end = outage.get("end") or outage.get("endTimestamp")
    ongoing = outage.get("ongoing")
    if ongoing is None:
        # The spec's own summary ("list of all outages for last month")
        # doesn't document a guaranteed "ongoing" field on every variant —
        # fall back to "no end timestamp yet" when the upstream object
        # doesn't carry one explicitly.
        ongoing = end is None
    return {
        "id": outage.get("id"),
        "type": outage.get("type"),
        "start": start,
        "end": end,
        "ongoing": ongoing,
        "device_id": outage.get("device", {}).get("id") or outage.get("deviceId"),
        "site_id": outage.get("site", {}).get("id") or outage.get("siteId"),
    }


def project_log(entry: dict) -> dict:
    return {
        "id": entry.get("id"),
        "timestamp": entry.get("timestamp") or entry.get("createdAt"),
        "level": entry.get("level"),
        "message": entry.get("message"),
        "tag": entry.get("tag"),
        "device_id": entry.get("device", {}).get("id") or entry.get("deviceId"),
        "site_id": entry.get("site", {}).get("id") or entry.get("siteId"),
    }


_PAGE_SIZE = 100


async def paginate_all(
    client: UispClient, path: str, max_items: int, page_size: int = _PAGE_SIZE
) -> tuple[list[Any], bool]:
    """Loop GET {path}?page=N&count=page_size against UISP's required
    page/count pagination, accumulating raw items until either the upstream
    API reports a short (last) page or ``max_items`` is reached.

    Internal to this server — the agent never sees raw page numbers (AC7,
    see docs/tickets/PRD-21265.md -> Decisions made by DEV01). Returns
    (scanned_items, truncated). ``truncated`` is only False when the loop
    stopped because upstream itself returned a page shorter than
    ``page_size`` (i.e. there is nothing more to scan) before the cap was
    hit; it is True whenever the cap was reached without that proof, even
    if that happens to be the exact last item upstream has.
    """
    scanned: list[Any] = []
    page = 0
    truncated = False
    while True:
        batch = await client.get(path, params={"page": page, "count": page_size})
        items = batch if isinstance(batch, list) else batch.get("data", [])
        if not items:
            break
        scanned.extend(items)
        is_last_page = len(items) < page_size
        if len(scanned) >= max_items:
            if len(scanned) > max_items:
                scanned = scanned[:max_items]
            truncated = not is_last_page
            break
        if is_last_page:
            break
        page += 1
    return scanned, truncated
