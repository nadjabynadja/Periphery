"""Thin async HTTP client for the Periphery backend API."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("PERIPHERY_API_URL", "http://127.0.0.1:8000")
DEFAULT_TOKEN = os.environ.get("PERIPHERY_API_TOKEN", "")
DEFAULT_ADMIN_KEY = os.environ.get("PERIPHERY_ADMIN_KEY", "")
DEFAULT_TIMEOUT = float(os.environ.get("PERIPHERY_API_TIMEOUT", "30"))


class PeripheryClient:
    """Lightweight async client wrapping the Periphery REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str = DEFAULT_TOKEN,
        admin_key: str = DEFAULT_ADMIN_KEY,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._admin_key = admin_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._admin_key:
            headers["X-Admin-Key"] = self._admin_key
        return headers

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self.base_url}{path}",
                json=body or {},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def delete(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.delete(
                f"{self.base_url}{path}",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()
