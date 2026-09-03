# Odysseus — Power BI & Qlik governance toolkit

One program, two platforms. Fills gaps native Power BI and Qlik tooling do
not cover reliably: usage data that expires before it can be reported on,
and Qlik Engine API access that keeps getting re-implemented per script
instead of being reused.

```
odysseus powerbi collect --interactive
odysseus powerbi raw-export
odysseus powerbi analytics
odysseus qlik extract --interactive
```

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in the section(s) for the platform(s) you use
python cli.py powerbi collect --interactive
python cli.py qlik extract --interactive
```

`build.bat` (Windows) produces a single standalone `dist\odysseus.exe` via
PyInstaller — no Python required to run it afterward.

## Platforms

| Package | Platform | Purpose |
|---|---|---|
| [`powerbi/`](powerbi/) | Power BI | Collects tenant activity events daily (Power BI Admin API retains only ~28 days) and accumulates them into permanent JSONL storage; aggregates them into exact usage tables (report views, per-user/report/day rollups). |
| [`qlik/`](qlik/) | Qlik Cloud | Extracts each app's load script, lineage, and data-model tables/fields/keys via the Engine API, then cross-checks the script's own QVD LOAD statements against the real final model to report, per app, which source QVD fields are actually used. Built on a reusable Engine API connector (`qlik/engine_client.py`) other Qlik tools can import rather than reimplement. See [`qlik/README.md`](qlik/README.md) for the field-usage confidence tiers and parser limitations. |

Each is a plain Python package under the shared `cli.py` entry point — no
separate executables, no separate dependency sets to install.

## Shared conventions

Both platforms follow the same governance-grade pattern for credentials:

- A secret is never hardcoded or logged.
- Credential source precedence: **interactive** (masked local prompt, for
  development) beats **Key Vault** (recommended — the secret never lives in
  code, env, or source control) beats a raw secret in **`.env`** (local
  prototyping only, explicitly never for sharing or committing).
- Each platform authenticates as a dedicated service identity (a Power BI
  Azure AD service principal; a Qlik Cloud OAuth machine-to-machine client)
  rather than a personal account or a long-lived personal API key, so access
  is attributable, scopable, and revocable independently of any one person.
- Key Vault secret names are prefixed per platform (`PBI_KEY_VAULT_*` /
  `QLIK_KEY_VAULT_*`), not shared — the two platforms' credentials are never
  the same secret, even if you point both at the same vault.
- `secure_input.py` (the masked `--interactive` credential dialog) is one
  shared module used by both platforms, not duplicated per tool.

## Open items

- **Qlik auth model assumption.** `qlik/` targets Qlik Cloud (Qlik Sense
  Enterprise SaaS) and authenticates via OAuth M2M client credentials. If any
  tenant in scope is Qlik Sense Enterprise on Windows instead, that tenant
  needs a certificate-based connector — a different auth module, not a
  config toggle on this one.
- **Scanner-API dimension tables** referenced by `powerbi/raw_export.py`'s
  `key_map.csv` (Workspaces, Reports, Datasets, Capacities, Apps) do not
  exist yet — noted in that module's own code as future work.
- **No orchestration.** Nothing here schedules itself. Daily/periodic
  execution is currently a manual or externally-scheduled responsibility
  (e.g. Windows Task Scheduler, or a future Fabric Notebook / Fabric
  pipeline trigger for the Power BI side).
