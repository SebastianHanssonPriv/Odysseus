from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Scope for an app-only (client credentials) token against the Power BI service.
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

# All admin operations hang off this base.
PBI_ADMIN_BASE = "https://api.powerbi.com/v1.0/myorg/admin"


@dataclass(frozen=True)
class Settings:
    # tenant_id / client_id may be None in interactive mode (prompted at runtime).
    tenant_id: str | None
    client_id: str | None
    auth_mode: str  # "interactive" | "key_vault" | "env_secret" | "managed_identity"
    key_vault_url: str | None
    key_vault_secret_name: str | None
    client_secret: str | None
    output_dir: Path


def load_settings(force_interactive: bool = False) -> Settings:
    tenant_id = os.environ.get("PBI_TENANT_ID", "").strip() or None
    client_id = os.environ.get("PBI_CLIENT_ID", "").strip() or None
    kv_url = os.environ.get("KEY_VAULT_URL", "").strip() or None
    kv_secret_name = os.environ.get("KEY_VAULT_SECRET_NAME", "").strip() or None
    env_secret = os.environ.get("PBI_CLIENT_SECRET", "").strip() or None
    declared_mode = os.environ.get("PBI_AUTH_MODE", "").strip().lower() or None

    interactive = force_interactive or declared_mode == "interactive"

    # Resolve the credential path. Interactive (runtime entry) wins when asked;
    # otherwise vault beats a raw env secret so a stray local secret can never
    # silently override the secure source.
    if interactive:
        auth_mode = "interactive"
    elif declared_mode == "managed_identity":
        auth_mode = "managed_identity"
    elif kv_url and kv_secret_name:
        auth_mode = "key_vault"
    elif env_secret:
        auth_mode = "env_secret"
    else:
        raise SystemExit(
            "No credential source configured. Use --interactive (local dev), or "
            "set KEY_VAULT_URL + KEY_VAULT_SECRET_NAME (recommended), or "
            "PBI_CLIENT_SECRET (local only), or PBI_AUTH_MODE=managed_identity."
        )

    # Non-interactive modes need the IDs up front; interactive prompts for them.
    if not interactive and (not tenant_id or not client_id):
        raise SystemExit(
            "Missing PBI_TENANT_ID / PBI_CLIENT_ID. Copy .env.example to .env "
            "and fill in the values, or run with --interactive."
        )

    return Settings(
        tenant_id=tenant_id,
        client_id=client_id,
        auth_mode=auth_mode,
        key_vault_url=kv_url,
        key_vault_secret_name=kv_secret_name,
        client_secret=env_secret,
        output_dir=Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser(),
    )
