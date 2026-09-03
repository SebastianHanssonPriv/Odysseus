from __future__ import annotations

import time

import requests

from .auth import PowerBITokenProvider
from .config import PBI_ADMIN_BASE

_MAX_RETRIES = 5


class PowerBIAdminClient:
    """Thin, throttle-aware wrapper over the Power BI Admin REST APIs.

    Honors HTTP 429 (Retry-After) and retries transient 5xx errors. The bearer
    token is fetched per request from the provider, which caches/refreshes it.
    """

    def __init__(self, token_provider: PowerBITokenProvider, base: str = PBI_ADMIN_BASE):
        self._tokens = token_provider
        self._base = base.rstrip("/")
        self._session = requests.Session()

    def get(self, path_or_url: str, params: dict | None = None) -> dict:
        # Continuation links come back as absolute URLs; everything else is a
        # path relative to the admin base.
        url = path_or_url if path_or_url.startswith("http") else f"{self._base}/{path_or_url.lstrip('/')}"

        for attempt in range(_MAX_RETRIES):
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            resp = self._session.get(url, headers=headers, params=params, timeout=120)

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

    def post(self, path: str, params: dict | None = None, json: dict | None = None) -> dict:
        url = f"{self._base}/{path.lstrip('/')}"

        for attempt in range(_MAX_RETRIES):
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            resp = self._session.post(url, headers=headers, params=params, json=json, timeout=120)

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
