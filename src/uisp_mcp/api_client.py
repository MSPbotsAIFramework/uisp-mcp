"""Client for Ubiquiti's official UISP Network API v2.1.

Every UISP installation (self-hosted or UISP Cloud-hosted) is its own
independently reachable instance — there is no Ubiquiti-operated shared
multi-tenant endpoint the way UniFi's ``api.ui.com`` connector proxies to a
console. So unlike a connector-style client, both the host (``base_url``)
and the credential (``api_key``) come from the caller's own request headers
and a ``UispClient`` is constructed fresh per request, never shared or
pooled across requests/tenants (see server.py's per-request contextvar).

Endpoint shapes (``basePath: /nms/api/v2.1``, ``x-auth-token`` header auth,
``GET /sites``, ``GET /sites/{id}``, ``GET /devices``, ``GET /devices/{id}``,
``GET /outages``, ``GET /logs``, the ``page``/``count`` pagination
parameters on list endpoints, and the ``{statusCode, error, message}`` error
schema) are taken directly from the UISP Network API's own generated
OpenAPI/Swagger document (every UISP instance self-serves it at
``https://<host>/nms/api-docs/``; a community-mirrored copy was used to
confirm field names while writing this client — see
``docs/tickets/PRD-21265.md`` Current state). Never exercised against a real
live UISP instance — no test account is available for this repo (see README
Known Gaps).
"""

import asyncio
from typing import Any

import httpx

from ._json import error_envelope

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

_API_PREFIX = "/nms/api/v2.1"

# status_code -> (error code, retryable). status_code 0 means a network/
# connection-level failure (no response at all).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def _classify(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    return "invalid_argument", False


class UispError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"UISP Network API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = _classify(self.status_code)
        return error_envelope(code, self.message, retryable)


class UispClient:
    """Async httpx client for one UISP instance, built fresh per request
    from that request's own ``X-Uisp-Base-Url`` / ``X-Uisp-Api-Key``
    headers.

    A new ``httpx.AsyncClient`` is created per call (not shared globally)
    because, unlike every other connector in this org, two concurrent
    requests here can legitimately target two entirely different hosts —
    there is nothing safe to pool across requests. TLS certificate
    verification is left ON (httpx's default); see
    ``docs/tickets/PRD-21265.md`` → Decisions made by DEV01 for why this is
    not relaxed for self-signed, self-hosted deployments.
    """

    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self._base_url}{_API_PREFIX}{path}"
        return await self._request("GET", url, params=params)

    async def _request(self, method: str, url: str, params: dict | None = None) -> Any:
        params = self._clean_params(params)
        headers = {"x-auth-token": self._api_key, "Accept": "application/json"}

        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await client.request(method, url, headers=headers, params=params)
                except httpx.RequestError as e:
                    last_exc = e
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                        continue
                    raise UispError(0, f"{e or type(e).__name__} (url={url})") from e

                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    delay = self._retry_delay(resp, attempt)
                    await asyncio.sleep(delay)
                    continue

                self._raise_for_status(resp)
                return self._parse_body(resp)

        # Unreachable in practice (loop always returns or raises above), but
        # keeps type checkers happy and guards against future edits.
        if last_exc:
            raise UispError(0, f"{last_exc}") from last_exc
        raise UispError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _extract_error(self, resp: httpx.Response) -> str:
        # UISP's own Error schema: {"statusCode": n, "error": "...", "message": "..."}
        try:
            detail = resp.json()
            if isinstance(detail, dict):
                return (
                    detail.get("message")
                    or detail.get("error")
                    or str(detail)
                )
            return str(detail)
        except ValueError:
            return resp.text

    def _parse_body(self, resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw_response": resp.text}

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise UispError(resp.status_code, self._extract_error(resp))
