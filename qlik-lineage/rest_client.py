from __future__ import annotations

import time
from typing import Iterator

import requests

from auth import QlikTokenProvider

_MAX_RETRIES = 5


class QlikRestClient:
    """Thin, throttle-aware wrapper over the Qlik Cloud REST API.

    Used only to enumerate apps (the Engine API has no "list every app"
    method of its own — it operates on one already-known app at a time).
    """

    def __init__(self, token_provider: QlikTokenProvider):
        self._tokens = token_provider
        self._base = token_provider.tenant_url
        self._session = requests.Session()

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        # Pagination links come back as absolute URLs; everything else is a
        # path relative to the tenant host.
        url = path_or_url if path_or_url.startswith("http") else f"{self._base}{path_or_url}"

        for attempt in range(_MAX_RETRIES):
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            resp = self._session.get(url, headers=headers, params=params, timeout=60)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "30"))
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"Gave up after {_MAX_RETRIES} retries: {url}")


def list_apps(client: QlikRestClient) -> Iterator[dict]:
    """Yield every app item in the tenant via the paginated Items API.

    Each item's "resourceId" is the underlying app id used to open an Engine
    API session — the item's own "id" is the catalog-entry id, not the app.
    """
    payload = client.get("/api/v1/items", params={"resourceType": "app", "limit": 100})
    while True:
        yield from payload.get("data", [])
        next_href = payload.get("links", {}).get("next", {}).get("href")
        if not next_href:
            break
        payload = client.get(next_href)
