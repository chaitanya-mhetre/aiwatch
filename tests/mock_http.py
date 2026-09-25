"""Offline HTTP for provider SDKs.

Each SDK gets an injected client whose transport is a ``MockTransport``, so a test can never reach
the network, even by mistake. (openai>=3 and anthropic>=1 use ``httpx2``; google-genai uses
``httpx``. Both expose the same ``MockTransport`` API.)
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import httpx2


class Route:
    def __init__(self, status: int = 200, body: Any = None, sse: bytes | None = None) -> None:
        self.status, self.body, self.sse = status, body, sse
        self.calls = 0


class Router:
    """Answers by URL-path suffix. Unmatched requests fail loudly."""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}

    def add(self, path_suffix: str, **kwargs: Any) -> Route:
        route = Route(**kwargs)
        self.routes[path_suffix] = route
        return route

    def _match(self, path: str) -> Route:
        for suffix, route in self.routes.items():
            if path.endswith(suffix):
                route.calls += 1
                return route
        raise AssertionError(f"unexpected request to {path}")

    def _parts(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        route = self._match(path)
        if route.sse is not None:
            return route.status, route.sse, {"content-type": "text/event-stream"}
        return route.status, json.dumps(route.body).encode(), {"content-type": "application/json"}

    def httpx2_client(self) -> httpx2.Client:
        def handler(req: httpx2.Request) -> httpx2.Response:
            status, content, headers = self._parts(req.url.path)
            return httpx2.Response(status, content=content, headers=headers)

        return httpx2.Client(transport=httpx2.MockTransport(handler))

    def httpx2_async_client(self) -> httpx2.AsyncClient:
        async def handler(req: httpx2.Request) -> httpx2.Response:
            status, content, headers = self._parts(req.url.path)
            return httpx2.Response(status, content=content, headers=headers)

        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    def httpx_client(self) -> httpx.Client:
        def handler(req: httpx.Request) -> httpx.Response:
            status, content, headers = self._parts(req.url.path)
            return httpx.Response(status, content=content, headers=headers)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def httpx_async_client(self) -> httpx.AsyncClient:
        async def handler(req: httpx.Request) -> httpx.Response:
            status, content, headers = self._parts(req.url.path)
            return httpx.Response(status, content=content, headers=headers)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
