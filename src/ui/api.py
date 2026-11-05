"""In-process API client for the UI (SPRINT-12.1 B-1).

The UI never touches repositories: every read/write goes through the public JSON API
over an ASGI transport bound to the running app (:func:`src.core.http.build_in_process_client`
— the one module allowed to import ``httpx`` under D-068). No network socket is opened.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from src.core.http import build_in_process_client

IN_PROCESS_BASE = "http://ui.internal"
CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: Any = None) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail


def error_code(payload: Any, status: int) -> str:
    """Extract the D-069 error code from a JSON error body (fallback ``http_<status>``)."""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        return detail["error"]
    if isinstance(detail, str) and detail:
        return detail
    return f"http_{status}"


class ApiClient:
    def __init__(
        self,
        app: FastAPI,
        *,
        prefix: str,
        access_token: str | None = None,
        cookies: dict[str, str] | None = None,
        client_ip: str = "127.0.0.1",
    ) -> None:
        headers = dict(CSRF_HEADERS)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._client = build_in_process_client(
            app,
            base_url=f"{IN_PROCESS_BASE}{prefix}",
            headers=headers,
            cookies=cookies,
            client_ip=client_ip,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """The raw response (``status_code`` / ``json()`` / ``cookies`` / ``content``)."""
        return await self._client.request(method, path, json=json, params=params, headers=headers)

    async def json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self.request(method, path, json=json, params=params, headers=headers)
        payload = _payload(response)
        status = response.status_code
        if status >= 400:
            raise ApiError(status, error_code(payload, status), payload)
        return payload

    async def get(self, path: str, **params: Any) -> Any:
        return await self.json("GET", path, params=params or None)

    async def post(self, path: str, body: Any = None) -> Any:
        return await self.json("POST", path, json=body)

    async def patch(self, path: str, body: Any) -> Any:
        return await self.json("PATCH", path, json=body)

    async def put(self, path: str, body: Any, *, headers: dict[str, str] | None = None) -> Any:
        return await self.json("PUT", path, json=body, headers=headers)

    async def delete(self, path: str, body: Any = None) -> Any:
        return await self.json("DELETE", path, json=body)


def _payload(response: Any) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return {"detail": response.text}
