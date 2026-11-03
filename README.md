# uisp-mcp

MCP server for **UISP** (Ubiquiti's ISP/MSP network management platform) —
site, device, outage, and event-log visibility, over the
[Model Context Protocol](https://modelcontextprotocol.io/) (Streamable HTTP
transport), built against UISP's own official **Network API v2.1** (every
UISP installation self-serves its own interactive Swagger UI at
`https://<host>/nms/api-docs/`).

Built for [PRD-21265](https://app.clickup.com/t/86e3v6wgu). Unlike
Ubiquiti's UniFi line, UISP has no shared multi-tenant cloud endpoint —
every deployment (self-hosted or UISP Cloud-hosted) is its own
independently reachable instance, so this server takes both the API token
and the instance's host as per-request headers (see Authentication below)
rather than resolving a console through a shared proxy.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Why two credential headers, not one

UniFi's connector proxy (`api.ui.com`) lets one account-level API key reach
any console adopted to that account. UISP has no equivalent — Ubiquiti does
not operate a shared cloud broker in front of customer UISP instances, even
for the UISP Cloud-hosted tier. Every deployment is reachable only at its
own host, so this server must be told *which* host on every request, not
just *which* token. See `X-Uisp-Base-Url` below.

## When would you use this

- "What sites/devices does this customer's UISP deployment have?" → `uisp_list_sites`, `uisp_list_devices`
- "Look up this specific site/device" → `uisp_get_site`, `uisp_get_device`
- "Has this site/device had any outages recently? Is anything down right now?" → `uisp_list_outages`
- "What's in this deployment's event/audit log?" → `uisp_list_logs`

**Not covered (read-only scope, see Known Gaps)**: any write/control action
(device restart, authorize/unauthorize, firewall/DHCP/QoS/interface
configuration, maintenance mode, backups), UCRM billing data, free-text
site search, per-device interface/traffic/statistics breakdown,
data-links, gateways, background tasks, firmware catalog, speed-test
history, and outage acknowledge/snooze (the API itself has no mutating
counterpart for outages).

## Tools

Authorization requires the two request headers `X-Uisp-Api-Key` / `X-Uisp-Base-Url` (see Authentication below).

| Tool | Description | Parameters |
|---|---|---|
| `uisp_list_sites` | List every site in this UISP instance | `site_type`/`ip` (optional) |
| `uisp_get_site` | Get full detail for one site | `site_id` (required) |
| `uisp_list_devices` | List devices, optionally scoped to one site | `site_id`/`role`/`type`/`authorized` (optional) |
| `uisp_get_device` | Get identification and current status overview for one device | `device_id` (required) |
| `uisp_list_outages` | List recent/ongoing network outages this instance retains | `device_id`/`in_progress`/`max_items` (optional) |
| `uisp_list_logs` | List recent event/audit log entries | `site_id`/`device_id`/`level`/`max_items` (optional) |

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m uisp_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

No credentials are required for the health endpoint.

## Authentication headers

Every request to `/mcp` must include the following HTTP headers (provided by the MCP caller/gateway):

| Header | Type | Required | Description |
|---|---|---|---|
| `X-Uisp-Api-Key` | string (password) | Yes | UISP API token, generated in the target UISP console at Settings → Users → your user → API tokens. A read-only token is sufficient and recommended for this server, since it issues no write calls. |
| `X-Uisp-Base-Url` | string | Yes | The full origin of the customer's UISP instance, e.g. `https://uisp.example.com` (no path suffix — the server appends `/nms/api/v2.1`). UISP has no shared multi-tenant cloud endpoint; every deployment, including Ubiquiti's own UISP Cloud-hosted tier, is reachable at its own host. |

Missing either header returns `401 Unauthorized` with both header names listed.

**Authentication mechanism**: UISP's own Network API is itself stateless single-header authentication — the received `X-Uisp-Api-Key` is passed through as-is as the upstream `x-auth-token` header, with no token exchange or session/cookie login step. Credentials are held only for the lifetime of a single request and never cached, logged, or persisted. TLS certificate verification stays ON by default for every target host (see Known Gaps for what this means for self-signed, self-hosted deployments).

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP)
- Headers: `X-Uisp-Api-Key`, `X-Uisp-Base-Url` (both required)

## Test Example

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Uisp-Api-Key: <uisp-api-token>" \
  -H "X-Uisp-Base-Url: https://<your-uisp-host>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": { "name": "uisp_list_sites", "arguments": {} }
  }'
```

> ⚠️ This repository is private; do not write real customer API tokens/hosts into any committed file,
> the placeholders above are for illustration only.

## Known Gaps

- **No real UISP instance was available to exercise the success path.**
  Every test in this repo stubs `UispClient` against the documented
  schema (confirmed via UISP's own published OpenAPI/Swagger document,
  `basePath: /nms/api/v2.1`, `x-auth-token` header auth — see
  `docs/tickets/PRD-21265.md` → Current state). The request/response shapes
  are taken from that spec, not guessed, but a real API token actually
  returning site/device/outage/log data has not been confirmed. Every
  existing connector in this org built without a test account
  (`unifi-network-mcp`) carries the same disclosed caveat.
- **Self-signed TLS certificates on self-hosted UISP instances will cause
  every call to fail with `upstream_error`.** TLS verification is left ON
  by default and deliberately not relaxed (see
  `docs/tickets/PRD-21265.md` → Decisions made by DEV01) — a customer
  reporting this is hitting a known, deliberate limitation, not a bug.
  There is no opt-in override mechanism yet.
- **`GET /sites` and `GET /devices` filtering (`site_type`/`ip` on sites;
  `role`/`type`/`authorized` on devices) is applied by this server itself**
  after fetching the full unfiltered list, not passed through as upstream
  query parameters — the mirrored OpenAPI document did not confirm
  server-side filter support for these fields, and getting that wrong would
  silently under- or over-return results. This costs nothing for the sizes
  these endpoints return in practice; if a deployment has a very large site
  or device count, every `uisp_list_sites`/`uisp_list_devices` call still
  fetches the whole list once per call.
- **`uisp_list_outages`/`uisp_list_logs` pagination is internal, capped by
  `max_items` (default 200), 100 items per upstream page.** The agent
  never sees raw page numbers (SOP §A4) and the response states
  `scanned`/`truncated` so a caller can tell whether more may exist beyond
  the cap. `device_id`/`in_progress`/`site_id`/`level` filters are applied
  by this server to the scanned set, after projection, not passed upstream.
- **No write/control endpoints of any kind are wrapped.** UISP's full API
  has dozens of mutating endpoints (device restart, authorize/unauthorize,
  firewall/DHCP/QoS/interface configuration, maintenance mode, backups);
  none are in scope for this first version — see Non-goals in
  `docs/tickets/PRD-21265.md`.
- **UCRM billing data is out of scope.** `Site.ucrm`/`ucrmDetails` and
  UISP's UCRM billing counterpart are a separate concern not wrapped here.
- **No free-text site search, no per-device interface/traffic/statistics
  breakdown, no data-links/gateways/background-tasks/firmware-catalog/
  speed-test coverage.** These are real, documented UISP endpoints (some
  are in the wider ~27-tool surface the third-party `WYRE-AI/uisp-mcp`
  project covers) but are outside this ticket's four-noun scope
  (site/device/outage/event-log); add them in a follow-up ticket if a real
  need shows up.
- **Outage history window is whatever UISP's own API retains** — its own
  spec summary for `GET /outages` says "last month"; this server does not
  extend that window.
- **No outage acknowledge/snooze workflow** — `GET /outages` is read-only
  in the spec itself; there is no mutating counterpart to wrap.
