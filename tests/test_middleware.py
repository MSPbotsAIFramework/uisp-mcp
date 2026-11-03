"""Gateway credential middleware tests: missing-header 401, and header
values correctly reaching the per-request contextvar (no global-state
leakage across requests) — including across two different UISP instances,
which is the scenario this server's architecture uniquely has to get right
(see api_client.py/server.py docstrings: there is no shared UISP cloud
endpoint, so a per-request client is not just an isolation nicety, it's the
only correct way to reach a second host at all).
"""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from uisp_mcp.__main__ import _build_http_app
from uisp_mcp.config import Settings
from uisp_mcp.server import create_mcp_server, get_client_from_context


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_headers_returns_401_with_required_headers_listed():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["required_headers"] == ["X-Uisp-Api-Key", "X-Uisp-Base-Url"]


def test_missing_single_header_still_returns_401():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "X-Uisp-Api-Key": "test-key",
                # X-Uisp-Base-Url intentionally omitted
            },
        )
        assert resp.status_code == 401


def test_header_present_reaches_request_context():
    from uisp_mcp.server import GatewayTokenMiddleware, _gateway_creds_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-uisp-api-key", b"test-key-123"),
                (b"x-uisp-base-url", b"https://uisp.example.com"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["creds"] == ("test-key-123", "https://uisp.example.com")
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_creds_var.get() is None


def test_client_factory_returns_none_without_context():
    settings = Settings()
    assert get_client_from_context(settings) is None


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_see_each_others_instance_sites():
    """TC-UISP-119 / AC11: two concurrent requests presenting two different
    X-Uisp-Api-Key / X-Uisp-Base-Url header pairs (two different UISP
    instances, i.e. two different hosts, not just two different tokens
    against one host) must never mix results, exercised through the real
    middleware -> contextvar -> UispClient-per-request path every tool goes
    through (api_client.py's base_url is baked into the client at
    construction) — not asserted from documentation.
    """
    import uisp_mcp.api_client as api_client_mod
    from uisp_mcp.server import GatewayTokenMiddleware
    from uisp_mcp.tools import sites
    from mcp.server.fastmcp import FastMCP

    settings = Settings()
    mcp = FastMCP(name="test")
    sites.register(mcp, lambda: get_client_from_context(settings))

    async def fake_get(self, path, params=None):
        # Yield control so the two concurrent calls below actually
        # interleave instead of running start-to-finish back to back.
        await asyncio.sleep(0)
        site_id = f"site-of-{self._base_url}"
        return [
            {
                "id": site_id,
                "name": site_id,
                "identification": {"type": "site"},
                "status": {"overall": "active"},
                "suspended": False,
            }
        ]

    original_get = api_client_mod.UispClient.get
    api_client_mod.UispClient.get = fake_get
    results: dict[str, str] = {}
    try:

        async def call_as(base_url: str, api_key: str) -> None:
            async def app(scope, receive, send):
                tool_result = await mcp.call_tool("uisp_list_sites", {})
                text = (
                    tool_result[0][0].text
                    if isinstance(tool_result, tuple)
                    else str(tool_result)
                )
                results[base_url] = text

            middleware = GatewayTokenMiddleware(app, settings)
            scope = {
                "type": "http",
                "path": "/mcp",
                "headers": [
                    (b"x-uisp-api-key", api_key.encode()),
                    (b"x-uisp-base-url", base_url.encode()),
                ],
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                pass

            await middleware(scope, receive, send)

        await asyncio.gather(
            call_as("https://uisp-a.example.com", "key-a"),
            call_as("https://uisp-b.example.com", "key-b"),
        )

        body_a = json.loads(results["https://uisp-a.example.com"])
        body_b = json.loads(results["https://uisp-b.example.com"])
        ids_a = {s["id"] for s in body_a["sites"]}
        ids_b = {s["id"] for s in body_b["sites"]}
        assert ids_a == {"site-of-https://uisp-a.example.com"}
        assert ids_b == {"site-of-https://uisp-b.example.com"}
        assert ids_a.isdisjoint(ids_b)
    finally:
        api_client_mod.UispClient.get = original_get
