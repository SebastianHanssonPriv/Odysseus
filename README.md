# Odysseus — Power BI & Qlik governance tooling

Small, purpose-built tools that fill gaps native Power BI and Qlik tooling do
not cover reliably: usage data that expires before it can be reported on, and
Qlik Engine API access that keeps getting re-implemented per script instead
of being reused.

## Tools

| Directory | Platform | Purpose |
|---|---|---|
| [`powerbi-usage/`](powerbi-usage/) | Power BI | Collects tenant activity events daily (Power BI Admin API retains only ~28 days) and accumulates them into permanent JSONL storage; aggregates them into exact usage tables (report views, per-user/report/day rollups). |
| [`qlik-lineage/`](qlik-lineage/) | Qlik Cloud | Extracts each app's load script, script lineage, and data-model tables/fields/keys via the Engine API, built on a reusable Engine API connector meant for future Qlik tools to import rather than reimplement. |

Each tool is self-contained: its own `.env.example`, `requirements.txt`, and
`build.bat` (PyInstaller `--onefile` build to a standalone Windows `.exe`).
See each tool's own README/module docstrings for setup and usage.

## Shared conventions

Both tools follow the same governance-grade pattern for credentials:

- A secret is never hardcoded or logged.
- Credential source precedence: **interactive** (masked local prompt, for
  development) beats **Key Vault** (recommended — the secret never lives in
  code, env, or source control) beats a raw secret in **`.env`** (local
  prototyping only, explicitly never for sharing or committing).
- Each tool authenticates as a dedicated service identity (a Power BI Azure
  AD service principal; a Qlik Cloud OAuth machine-to-machine client) rather
  than a personal account or a long-lived personal API key, so access is
  attributable, scopable, and revocable independently of any one person.

## Open items

- **Qlik auth model assumption.** `qlik-lineage` targets Qlik Cloud (Qlik
  Sense Enterprise SaaS) and authenticates via OAuth M2M client credentials.
  If any tenant in scope is Qlik Sense Enterprise on Windows instead, that
  tenant needs a certificate-based connector — a different auth module, not
  a config toggle on this one.
- **Scanner-API dimension tables** referenced by `powerbi-usage`'s
  `key_map.csv` (Workspaces, Reports, Datasets, Capacities, Apps) do not
  exist yet — noted in that tool's own code as future work.
- **No orchestration.** Neither tool schedules itself. Daily/periodic
  execution is currently a manual or externally-scheduled responsibility
  (e.g. Windows Task Scheduler, or a future Fabric Notebook / Fabric
  pipeline trigger for the Power BI tool).
