from __future__ import annotations

import time
import warnings

from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential

from .config import POWERBI_SCOPE, Settings


class PowerBITokenProvider:
    """Supplies bearer tokens for the Power BI Admin APIs.

    The client secret is never read from source code. It is resolved at runtime
    from the most secure source configured, and cached tokens are refreshed a
    few minutes before expiry.
    """

    def __init__(self, settings: Settings):
        self._credential: TokenCredential = self._build_credential(settings)
        self._token: str | None = None
        self._expires_on: float = 0.0

    def _build_credential(self, settings: Settings) -> TokenCredential:
        if settings.auth_mode == "interactive":
            # Local development: collect credentials through a masked window
            # (copy/cut disabled, memory only — never persisted). IDs already in
            # .env are reused so only the secret needs typing.
            from secure_input import prompt_credentials

            fields = []
            if not settings.tenant_id:
                fields.append(("tenant_id", "Tenant ID"))
            if not settings.client_id:
                fields.append(("client_id", "Client ID"))
            fields.append(("client_secret", "Client secret"))
            entered = prompt_credentials("Power BI credentials (development)", fields)
            if not entered:
                raise SystemExit("Credential entry cancelled.")
            tenant = settings.tenant_id or entered["tenant_id"]
            client = settings.client_id or entered["client_id"]
            return ClientSecretCredential(tenant, client, entered["client_secret"])

        if settings.auth_mode == "managed_identity":
            # No secret anywhere: the runtime identity (Fabric workspace identity
            # / Azure managed identity) is itself the service principal in the
            # Power BI admin security group. The most secure option.
            return DefaultAzureCredential()

        if settings.auth_mode == "key_vault":
            secret = self._read_secret_from_vault(settings)
            return ClientSecretCredential(settings.tenant_id, settings.client_id, secret)

        # env_secret — local prototyping only.
        warnings.warn(
            "Using PBI_CLIENT_SECRET from the environment. This is for local "
            "prototyping only — move the secret to Key Vault before sharing.",
            stacklevel=2,
        )
        return ClientSecretCredential(
            settings.tenant_id, settings.client_id, settings.client_secret
        )

    @staticmethod
    def _read_secret_from_vault(settings: Settings) -> str:
        # Lazy import so the Key Vault dependency is only needed on this path.
        from azure.keyvault.secrets import SecretClient

        # The human/managed identity unlocks the vault; the vaulted secret then
        # authenticates the Power BI service principal. Two separate trust hops,
        # and the secret never touches disk or source control.
        vault_credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.key_vault_url, credential=vault_credential)
        secret = client.get_secret(settings.key_vault_secret_name).value
        if not secret:
            raise SystemExit(
                f"Key Vault secret '{settings.key_vault_secret_name}' is empty."
            )
        return secret

    def token(self) -> str:
        if not self._token or time.time() > self._expires_on - 300:
            access: AccessToken = self._credential.get_token(POWERBI_SCOPE)
            self._token = access.token
            self._expires_on = access.expires_on
        return self._token
