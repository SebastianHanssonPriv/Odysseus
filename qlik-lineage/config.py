from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Qlik Cloud's OAuth token endpoint lives on the tenant host itself.
QLIK_OAUTH_TOKEN_PATH = "/oauth/token"


@dataclass(frozen=True)
class Settings:
    # tenant_url / oauth_client_id may be None in interactive mode (prompted).
    tenant_url: str | None
    oauth_client_id: str | None
    auth_mode: str  # "interactive" | "key_vault" | "env_secret"
    key_vault_url: str | None
    key_vault_secret_name: str | None
    oauth_client_secret: str | None
    oauth_scope: str | None
    output_dir: Path


def load_settings(force_interactive: bool = False) -> Settings:
    tenant_url = os.environ.get("QLIK_TENANT_URL", "").strip().rstrip("/") or None
    client_id = os.environ.get("QLIK_OAUTH_CLIENT_ID", "").strip() or None
    kv_url = os.environ.get("KEY_VAULT_URL", "").strip() or None
    kv_secret_name = os.environ.get("KEY_VAULT_SECRET_NAME", "").strip() or None
    env_secret = os.environ.get("QLIK_OAUTH_CLIENT_SECRET", "").strip() or None
    scope = os.environ.get("QLIK_OAUTH_SCOPE", "").strip() or None
    declared_mode = os.environ.get("QLIK_AUTH_MODE", "").strip().lower() or None

    interactive = force_interactive or declared_mode == "interactive"

    # Same precedence contract as the Power BI tool: interactive (explicit ask)
    # beats Key Vault (recommended), which beats a raw secret in .env (local
    # prototyping only) — a stray local secret can never silently override the
    # secure source. Qlik Cloud has no managed-identity equivalent for an
    # external OAuth client, so that tier does not exist here; the M2M OAuth
    # client itself is the "service principal" of this tool.
    if interactive:
        auth_mode = "interactive"
    elif kv_url and kv_secret_name:
        auth_mode = "key_vault"
    elif env_secret:
        auth_mode = "env_secret"
    else:
        raise SystemExit(
            "No credential source configured. Use --interactive (local dev), or "
            "set KEY_VAULT_URL + KEY_VAULT_SECRET_NAME (recommended), or "
            "QLIK_OAUTH_CLIENT_SECRET (local only)."
        )

    if not interactive and (not tenant_url or not client_id):
        raise SystemExit(
            "Missing QLIK_TENANT_URL / QLIK_OAUTH_CLIENT_ID. Copy .env.example to "
            ".env and fill in the values, or run with --interactive."
        )

    return Settings(
        tenant_url=tenant_url,
        oauth_client_id=client_id,
        auth_mode=auth_mode,
        key_vault_url=kv_url,
        key_vault_secret_name=kv_secret_name,
        oauth_client_secret=env_secret,
        oauth_scope=scope,
        output_dir=Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser(),
    )
