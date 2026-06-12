"""Read-only HTTP base for external clients (FR-027).

Every external client issues **only** GET requests. This base exposes a single
``_get`` helper and deliberately offers no post/put/delete/patch surface, so a
mutating request is structurally impossible. ``tests/unit/test_read_only.py``
asserts the guarantee (T016).
"""

from __future__ import annotations

from typing import Any

import httpx


class ReadOnlyClient:
    """Wraps an ``httpx.AsyncClient`` and permits GET only.

    The client is injected so tests can supply a respx-mocked transport.
    """

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        return (await self._get(url, params=params)).json()

    async def _get_text(self, url: str, *, params: dict[str, Any] | None = None) -> str:
        return (await self._get(url, params=params)).text
