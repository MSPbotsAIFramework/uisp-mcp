"""tools/list snapshot + per-tool behavior tests.

No network calls: tool enumeration goes through FastMCP's in-process
list_tools(), and every tool is exercised against a stub UispClient (no
real HTTP). Covers AC1-AC10, AC12 (test case ids TC-UISP-101..108,
113..118, 120 in docs/tickets/PRD-21265.ac.json). AC11 (TC-UISP-119) is in
test_middleware.py.
"""

import json

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from uisp_mcp.api_client import UispClient, UispError
from uisp_mcp.config import Settings
from uisp_mcp.server import create_mcp_server
from uisp_mcp.tools._common import paginate_all

# name -> (required params, expected annotation hint set to True)
EXPECTED_TOOLS = {
    "uisp_list_sites": (set(), {"readOnlyHint"}),
    "uisp_get_site": ({"site_id"}, {"readOnlyHint"}),
    "uisp_list_devices": (set(), {"readOnlyHint"}),
    "uisp_get_device": ({"device_id"}, {"readOnlyHint"}),
    "uisp_list_outages": (set(), {"readOnlyHint"}),
    "uisp_list_logs": (set(), {"readOnlyHint"}),
}


@pytest.mark.asyncio
async def test_tools_list_snapshot_all_read_only():
    """TC-UISP-120 / AC12."""
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"
    assert len(names) == 6

    by_name = {t.name: t for t in tools}
    for name, (expected_required, expected_hints) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"

        description = tool.description or ""
        assert len(description) <= 500, f"{name}: description too long ({len(description)})"
        first_line = description.strip().splitlines()[0] if description.strip() else ""
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"
        assert "GET /" not in description and "POST /" not in description, (
            f"{name}: leaked implementation detail"
        )

        annotations = tool.annotations
        actual_hints = set()
        if annotations is not None:
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint"):
                if getattr(annotations, hint, None) is True:
                    actual_hints.add(hint)
        assert actual_hints == expected_hints, f"{name}: hints={actual_hints}"

        name_lower = name.lower()
        for bad in ("delete", "restart", "reboot", "authorize", "create", "update", "set_"):
            assert bad not in name_lower, f"{name}: tool name implies a write action"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


def test_error_envelope_mapping():
    cases = [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ]
    for status_code, expected_code, expected_retryable in cases:
        err = UispError(status_code, "boom")
        envelope = json.loads(err.to_envelope())
        assert envelope["error"]["code"] == expected_code
        assert envelope["error"]["retryable"] is expected_retryable
        assert envelope["error"]["message"] == "boom"


# --- stub client ------------------------------------------------------


class _StubClient:
    """Maps a path to a canned response, or raises a canned error.

    ``responses`` and ``paged`` are keyed by path. ``paged[path]`` is the
    full list of raw items the real upstream would eventually hand back
    across pages; this stub slices it the same way the real page/count
    pagination would.
    """

    def __init__(self, responses: dict | None = None, paged: dict | None = None, error=None):
        self._responses = responses or {}
        self._paged = paged or {}
        self._error = error
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def get(self, path, params=None):
        params = params or {}
        self.calls.append((path, dict(params)))
        if self._error is not None:
            raise self._error
        if path in self._paged:
            items = self._paged[path]
            page = params.get("page", 0)
            count = params.get("count", 100)
            start = page * count
            return items[start : start + count]
        return self._responses[path]

    async def aclose(self):
        # Every tools/*.py module calls this in a ``finally`` block
        # (review round 1 must-fix #1); the stub tracks it so tests can
        # assert the real contract is honored even without a real client.
        self.closed = True


async def _call(mcp: FastMCP, name: str, args: dict) -> str:
    result = await mcp.call_tool(name, args)
    return result[0][0].text if isinstance(result, tuple) else str(result)


def _mcp_with(module_name: str, client):
    from uisp_mcp.tools import devices, logs, outages, sites

    modules = {
        "sites": sites,
        "devices": devices,
        "outages": outages,
        "logs": logs,
    }
    mcp = FastMCP(name="test")
    modules[module_name].register(mcp, lambda: client)
    return mcp


# --- uisp_list_sites / uisp_get_site -----------------------------------


def _site(site_id, site_type="site", status="active", suspended=False):
    return {
        "id": site_id,
        "name": site_id,
        "identification": {"type": site_type},
        "status": {"overall": status},
        "suspended": suspended,
        "updated": "2026-11-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_list_sites_returns_all_and_filters_by_type():
    """TC-UISP-101 / AC1."""
    sites = [_site("s1", "site"), _site("s2", "site"), _site("s3", "endpoint")]
    client = _StubClient(responses={"/sites": sites})
    mcp = _mcp_with("sites", client)

    body = json.loads(await _call(mcp, "uisp_list_sites", {}))
    assert body["count"] == 3
    assert {s["id"] for s in body["sites"]} == {"s1", "s2", "s3"}

    body = json.loads(await _call(mcp, "uisp_list_sites", {"site_type": "endpoint"}))
    assert body["count"] == 1
    assert body["sites"][0]["id"] == "s3"


@pytest.mark.asyncio
async def test_get_site_returns_detail_fields():
    """TC-UISP-102 / AC2."""
    client = _StubClient(responses={"/sites/s1": _site("s1", "site", "active", False)})
    mcp = _mcp_with("sites", client)
    body = json.loads(await _call(mcp, "uisp_get_site", {"site_id": "s1"}))
    assert body["name"] == "s1"
    assert body["type"] == "site"
    assert body["status"] == "active"
    assert body["suspended"] is False


@pytest.mark.asyncio
async def test_get_site_unknown_id_returns_structured_not_found():
    """TC-UISP-103 / AC2."""
    client = _StubClient(error=UispError(404, "Site not found"))
    mcp = _mcp_with("sites", client)
    body = json.loads(await _call(mcp, "uisp_get_site", {"site_id": "nope"}))
    assert body["error"]["code"] == "not_found"


# --- uisp_list_devices / uisp_get_device -------------------------------


def _device(device_id, site_id, role="ap", device_type="airmax", status="ONLINE"):
    return {
        "identification": {
            "id": device_id,
            "name": device_id,
            "model": "M",
            "role": role,
            "type": device_type,
            "site": {"id": site_id},
            "authorized": True,
        },
        "overview": {
            "status": status,
            "uptime": 1000,
            "cpu": 5,
            "ram": 10,
            "outageScore": 0,
            "lastSeen": "2026-11-02T00:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_list_devices_filters_by_site_id():
    """TC-UISP-104 / AC3."""
    devices = [_device("d1", "s1"), _device("d2", "s1"), _device("d3", "s2")]
    client = _StubClient(responses={"/devices": devices})
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_list_devices", {"site_id": "s1"}))
    assert body["count"] == 2
    assert {d["id"] for d in body["devices"]} == {"d1", "d2"}


@pytest.mark.asyncio
async def test_list_devices_without_site_id_returns_all_sites():
    """TC-UISP-105 / AC3."""
    devices = [_device("d1", "s1"), _device("d2", "s2")]
    client = _StubClient(responses={"/devices": devices})
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_list_devices", {}))
    assert body["count"] == 2
    assert {d["site_id"] for d in body["devices"]} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_list_devices_filters_by_role():
    """TC-UISP-106 / AC3."""
    devices = [
        _device("d1", "s1", role="ap"),
        _device("d2", "s1", role="router"),
        _device("d3", "s1", role="ap"),
    ]
    client = _StubClient(responses={"/devices": devices})
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_list_devices", {"role": "router"}))
    assert body["count"] == 1
    assert body["devices"][0]["id"] == "d2"


@pytest.mark.asyncio
async def test_list_devices_empty_site_returns_empty_list_not_error():
    """TC-UISP-118 / AC10."""
    devices = [_device("d1", "s-other")]
    client = _StubClient(responses={"/devices": devices})
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_list_devices", {"site_id": "s-empty"}))
    assert body["devices"] == []
    assert body["count"] == 0
    assert "error" not in body


@pytest.mark.asyncio
async def test_get_device_returns_identification_and_overview():
    """TC-UISP-107 / AC4."""
    client = _StubClient(responses={"/devices/d1": _device("d1", "s1")})
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_get_device", {"device_id": "d1"}))
    assert body["id"] == "d1"
    assert body["overview"]["uptime"] == 1000
    assert body["overview"]["cpu"] == 5
    assert body["overview"]["outageScore"] == 0


@pytest.mark.asyncio
async def test_get_device_unknown_id_returns_structured_not_found():
    """TC-UISP-108 / AC4."""
    client = _StubClient(error=UispError(404, "Device not found"))
    mcp = _mcp_with("devices", client)
    body = json.loads(await _call(mcp, "uisp_get_device", {"device_id": "nope"}))
    assert body["error"]["code"] == "not_found"


# --- uisp_list_outages ---------------------------------------------------


def _outage(outage_id, device_id, site_id, ongoing, outage_type="outage"):
    return {
        "id": outage_id,
        "type": outage_type,
        "start": "2026-11-01T00:00:00Z",
        "end": None if ongoing else "2026-11-01T01:00:00Z",
        "ongoing": ongoing,
        "device": {"id": device_id},
        "site": {"id": site_id},
    }


@pytest.mark.asyncio
async def test_list_outages_filters_in_progress():
    """TC-UISP-109 / AC5."""
    outages = [
        _outage("o1", "d1", "s1", ongoing=True, outage_type="outage"),
        _outage("o2", "d1", "s1", ongoing=False, outage_type="unreachable"),
    ]
    client = _StubClient(paged={"/outages": outages})
    mcp = _mcp_with("outages", client)
    body = json.loads(await _call(mcp, "uisp_list_outages", {"in_progress": True}))
    assert len(body["outages"]) == 1
    assert body["outages"][0]["id"] == "o1"


@pytest.mark.asyncio
async def test_list_outages_filters_by_device_id():
    """TC-UISP-110 / AC5."""
    outages = [
        _outage("o1", "d1", "s1", ongoing=True),
        _outage("o2", "d2", "s1", ongoing=True),
    ]
    client = _StubClient(paged={"/outages": outages})
    mcp = _mcp_with("outages", client)
    body = json.loads(await _call(mcp, "uisp_list_outages", {"device_id": "d2"}))
    assert len(body["outages"]) == 1
    assert body["outages"][0]["device_id"] == "d2"


@pytest.mark.asyncio
async def test_list_outages_stops_at_max_items_and_reports_truncated():
    """TC-UISP-113 / AC7: 250 outages exist upstream (page size 100 is
    this server's internal constant), max_items defaults to 200 -> scanned
    == 200, truncated is True, no error/hang."""
    outages = [_outage(f"o{i}", "d1", "s1", ongoing=False) for i in range(250)]
    client = _StubClient(paged={"/outages": outages})
    mcp = _mcp_with("outages", client)
    body = json.loads(await _call(mcp, "uisp_list_outages", {}))
    assert body["scanned"] == 200
    assert body["truncated"] is True
    assert len(client.calls) == 2


# --- uisp_list_logs -------------------------------------------------------


def _log(log_id, site_id, device_id, level="info"):
    return {
        "id": log_id,
        "timestamp": "2026-11-01T00:00:00Z",
        "level": level,
        "message": f"event {log_id}",
        "tag": "system",
        "device": {"id": device_id},
        "site": {"id": site_id},
    }


@pytest.mark.asyncio
async def test_list_logs_filters_by_level():
    """TC-UISP-111 / AC6."""
    logs = [
        _log("l1", "s1", "d1", level="info"),
        _log("l2", "s1", "d1", level="warning"),
        _log("l3", "s1", "d1", level="error"),
    ]
    client = _StubClient(paged={"/logs": logs})
    mcp = _mcp_with("logs", client)
    body = json.loads(await _call(mcp, "uisp_list_logs", {"level": "error"}))
    assert len(body["logs"]) == 1
    assert body["logs"][0]["id"] == "l3"


@pytest.mark.asyncio
async def test_list_logs_filters_by_site_id():
    """TC-UISP-112 / AC6."""
    logs = [_log("l1", "s1", "d1"), _log("l2", "s2", "d2")]
    client = _StubClient(paged={"/logs": logs})
    mcp = _mcp_with("logs", client)
    body = json.loads(await _call(mcp, "uisp_list_logs", {"site_id": "s2"}))
    assert len(body["logs"]) == 1
    assert body["logs"][0]["site_id"] == "s2"


@pytest.mark.asyncio
async def test_list_logs_scans_past_first_page_without_truncating():
    """TC-UISP-114 / AC7: 150 log entries exist upstream, max_items
    defaults to 200 -> all 150 returned, scanned == 150, truncated is
    False."""
    logs = [_log(f"l{i}", "s1", "d1") for i in range(150)]
    client = _StubClient(paged={"/logs": logs})
    mcp = _mcp_with("logs", client)
    body = json.loads(await _call(mcp, "uisp_list_logs", {}))
    assert body["scanned"] == 150
    assert body["truncated"] is False
    assert len(body["logs"]) == 150
    assert len(client.calls) == 2


# --- credential / error-mapping cross-tool tests --------------------------

_ALL_TOOLS = [
    ("sites", "uisp_list_sites", {}),
    ("sites", "uisp_get_site", {"site_id": "s1"}),
    ("devices", "uisp_list_devices", {}),
    ("devices", "uisp_get_device", {"device_id": "d1"}),
    ("outages", "uisp_list_outages", {}),
    ("logs", "uisp_list_logs", {}),
]


@pytest.mark.asyncio
async def test_tools_return_not_configured_when_credentials_missing():
    """TC-UISP-115 / AC8: every tool, not just one."""
    for module_name, tool_name, args in _ALL_TOOLS:
        mcp = _mcp_with(module_name, None)
        body = json.loads(await _call(mcp, tool_name, args))
        assert body["error"]["code"] == "not_configured", tool_name


@pytest.mark.asyncio
async def test_tools_map_upstream_401_to_unauthorized():
    """TC-UISP-116 / AC9: every tool, not just one."""
    for module_name, tool_name, args in _ALL_TOOLS:
        client = _StubClient(error=UispError(401, "bad token"))
        mcp = _mcp_with(module_name, client)
        body = json.loads(await _call(mcp, tool_name, args))
        assert body["error"]["code"] == "unauthorized", tool_name


@pytest.mark.asyncio
async def test_tools_map_connection_failure_to_upstream_error():
    """TC-UISP-117 / AC9: every tool, not just one."""
    for module_name, tool_name, args in _ALL_TOOLS:
        client = _StubClient(error=UispError(0, "connection refused"))
        mcp = _mcp_with(module_name, client)
        body = json.loads(await _call(mcp, tool_name, args))
        assert body["error"]["code"] == "upstream_error", tool_name


def test_client_base_url_and_auth_header_shape():
    client = UispClient("https://uisp.example.com", "test-token")
    assert client._base_url == "https://uisp.example.com"
    assert client._api_key == "test-token"


# --- review round 1 must-fix regressions -----------------------------------
# docs/tickets/PRD-21265-review.md Round 1


@pytest.mark.asyncio
async def test_every_tool_closes_its_client():
    """TC-UISP-121 (source: dev). Review round 1 must-fix #1 is "reuse one
    HTTP client within a request, don't open one per endpoint call"; the
    other half of that contract is that whatever client was opened for the
    call must get closed afterwards. Every tools/*.py module now does this
    in a ``finally`` around client_factory()."""
    for module_name, tool_name, args in _ALL_TOOLS:
        client = _StubClient(
            responses={
                "/sites": [],
                "/sites/s1": _site("s1", "site", "active", False),
                "/devices": [],
                "/devices/d1": _device("d1", "s1"),
            },
            paged={"/outages": [], "/logs": []},
        )
        mcp = _mcp_with(module_name, client)
        await _call(mcp, tool_name, args)
        assert client.closed, f"{tool_name} did not close its UispClient"


@pytest.mark.asyncio
async def test_real_client_reuses_one_http_connection_across_calls(monkeypatch):
    """TC-UISP-122 (source: dev). Review round 1 must-fix #1, exercised
    against the real UispClient (not the stub): paginate_all makes 2+
    upstream calls for one tool invocation, and must not open a new
    httpx.AsyncClient per page."""
    created: list[object] = []
    orig_init = httpx.AsyncClient.__init__

    def counting_init(self, *args, **kwargs):
        created.append(self)
        orig_init(self, *args, **kwargs)

    async def fake_request(self, method, url, headers=None, params=None):
        page = (params or {}).get("page", 0)
        items = [{"id": f"o{page}"}] if page == 0 else []
        return httpx.Response(200, json=items, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    client = UispClient("https://uisp.example.com", "test-token")
    try:
        scanned, truncated = await paginate_all(client, "/outages", max_items=200)
    finally:
        await client.aclose()

    assert len(scanned) == 1
    assert truncated is False
    assert len(created) == 1, (
        f"expected exactly one httpx.AsyncClient for a 2-page paginate_all call, got {len(created)}"
    )


def test_retry_backoff_worst_case_stays_under_sop_120s_cap():
    """TC-UISP-123 (source: dev). Review round 1 must-fix #2: a single tool
    call's total upstream time, including every retry and the worst-case
    Retry-After-driven backoff, must provably stay under the SOP's 120s
    hard cap (vendor-mcp-development-sop.md Sec 5 / §14 checklist)."""
    from uisp_mcp.api_client import _MAX_BACKOFF_SECONDS, _MAX_RETRIES, _TIMEOUT

    n_attempts = _MAX_RETRIES + 1
    per_attempt_worst_case = _TIMEOUT.connect + _TIMEOUT.read
    worst_case_total = n_attempts * per_attempt_worst_case + (n_attempts - 1) * _MAX_BACKOFF_SECONDS
    assert worst_case_total < 120.0, (
        f"worst-case call duration {worst_case_total}s exceeds the SOP's 120s cap"
    )
