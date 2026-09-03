from __future__ import annotations

import time
import warnings

import requests

from config import QLIK_OAUTH_TOKEN_PATH, Settings

_TOKEN_REFRESH_MARGIN_SECONDS = 300


class QlikTokenProvider:
    """Supplies bearer tokens for the Qlik Cloud Engine and REST APIs.

    Qlik Cloud has no managed-identity equivalent, so the recommended path is
    an OAuth machine-to-machine (M2M) client using the client credentials
    grant, with its secret held in Key Vault — the Qlik-side counterpart to
    the Power BI tool's service principal. The client secret is never read
    from source code.
    """

    def __init__(self, settings: Settings):
        self._scope = settings.oauth_scope
        self._tenant_url, self._client_id, self._client_secret = self._resolve_credential(
            settings
        )
        self._token: str | None = None
        self._expires_at: float = 0.0

    def _resolve_credential(self, settings: Settings) -> tuple[str, str, str]:
        if settings.auth_mode == "interactive":
            # Local development: collect credentials through a masked window
            # (copy/cut disabled, memory only — never persisted).
            from secure_input import prompt_credentials

            entered = prompt_credentials(
                need_tenant=not settings.tenant_url,
                need_client=not settings.oauth_client_id,
            )
            if not entered:
                raise SystemExit("Credential entry cancelled.")
            tenant = (settings.tenant_url or entered["tenant_url"]).rstrip("/")
            client_id = settings.oauth_client_id or entered["client_id"]
            return tenant, client_id, entered["client_secret"]

        if settings.auth_mode == "key_vault":
            secret = self._read_secret_from_vault(settings)
            return settings.tenant_url, settings.oauth_client_id, secret

        # env_secret — local prototyping only.
        warnings.warn(
            "Using QLIK_OAUTH_CLIENT_SECRET from the environment. This is for "
            "local prototyping only — move the secret to Key Vault before sharing.",
            stacklevel=2,
        )
        return settings.tenant_url, settings.oauth_client_id, settings.oauth_client_secret

    @staticmethod
    def _read_secret_from_vault(settings: Settings) -> str:
        # Lazy imports so the Key Vault dependency is only needed on this path.
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        vault_credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.key_vault_url, credential=vault_credential)
        secret = client.get_secret(settings.key_vault_secret_name).value
        if not secret:
            raise SystemExit(
                f"Key Vault secret '{settings.key_vault_secret_name}' is empty."
            )
        return secret

    def token(self) -> str:
        if not self._token or time.time() > self._expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
            self._token, ttl_seconds = self._request_token()
            self._expires_at = time.time() + ttl_seconds
        return self._token

    def _request_token(self) -> tuple[str, int]:
        body = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        if self._scope:
            body["scope"] = self._scope

        resp = requests.post(
            f"{self._tenant_url}{QLIK_OAUTH_TOKEN_PATH}",
            json=body,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload["access_token"], int(payload.get("expires_in", 3600))

    @property
    def tenant_url(self) -> str:
        return self._tenant_url
