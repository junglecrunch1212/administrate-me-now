# Prompt 17: CLI + deployment + migrations

**Phase:** BUILD.md "CLI SURFACE" + PHASE 14 (CLI subcommands) + migration framework.
**Depends on:** Prompt 16. Bootstrap produces a working instance.
**Estimated duration:** 4-5 hours.
**Stop condition:** `adminme` CLI installed and working; 16 subcommand groups per BUILD.md all return help text and execute their happy paths; deploy script (`deploy.sh`) pulls from GitHub, runs migrations, restarts services; migration framework applies schema changes safely.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"CLI SURFACE"** or wherever PHASE 14 is listed — the full subcommand inventory.
2. `ADMINISTRATEME_FIELD_MANUAL.md` chapter 7 (daily/weekly/monthly rhythm) — the CLI is the operator's surface for these operations; design the CLI to match.
3. `ADMINISTRATEME_BUILD.md` migration notes (search "migration" — there are scattered references about L2, projections, xlsx sidecar all being versionable).

## Operating context

Once an instance is bootstrapped, operators manage it via the `adminme` CLI. Every recurring operation has a subcommand: instance (status/restart/back up/restore), event (append, query, replay), projection (status, rebuild, query), skills (install, list, test), pipelines (list, test, trigger), plaid (link, unlink, health), reminders (sync, list-map, pause/resume), member (add, remove, update, view-as), pack (install, uninstall, list, validate), service (start, stop, status, logs), cost (ledger, forecast), observation (on, off, log), xlsx (forward-sync, reverse-sync, verify), migration (list, apply, verify, dry-run), openclaw (audit, re-register, health), deploy (pull, apply, rollback).

## Objective

Build the full CLI + deploy script + migration framework.

## Out of scope

- Third-party plugins for the CLI (operators add as packs).
- GUI alternatives — CLI only.

## Deliverables

### `adminme/cli/main.py`

Typer-based app. 16 subcommand groups:

```
adminme instance {status, restart, backup, restore, info}
adminme event {append, query, replay, count-by-type, dump}
adminme projection {status, rebuild, query, list}
adminme skill {install, list, test, show, uninstall}
adminme pipeline {list, test, trigger, status}
adminme plaid {link, unlink, health, cursor, sync-now}
adminme reminders {sync, list-map, pause, resume, status}
adminme member {add, remove, update, view-as, list}
adminme pack {install, uninstall, list, validate}
adminme service {start, stop, status, logs, tail}
adminme cost {ledger, forecast, by-skill, by-day}
adminme observation {on, off, log, status}
adminme xlsx {forward-sync, reverse-sync, verify, status}
adminme migration {list, apply, verify, dry-run}
adminme openclaw {audit, re-register, health, persona-list}
adminme deploy {pull, apply, rollback, status}
```

Each subcommand:
- Rich help text.
- `--lab` flag where destructive: enables lab-only behavior (or requires it).
- Structured output option: `--json` for programmatic use.
- Dry-run option where destructive: `--dry-run`.

### Migration framework

`adminme/lib/migrations.py`:

```python
@dataclass
class Migration:
    id: str             # "0042_add_commitment_strength_field"
    description: str
    applies_to: Literal["event_log", "projection.parties", "projection.xlsx_workbooks", ...]
    forward_sql: str | None   # for SQL-based migrations
    forward_code: Callable | None  # for Python migrations (data transformation, projection rebuild, etc.)
    reverse_sql: str | None
    reverse_code: Callable | None
    requires_projection_rebuild: bool = False

class MigrationRunner:
    async def list_pending(self) -> list[Migration]: ...
    async def apply(self, migration_id: str, dry_run: bool = False) -> MigrationResult: ...
    async def apply_all_pending(self, dry_run: bool = False) -> list[MigrationResult]: ...
    async def verify(self, migration_id: str) -> bool: ...
```

Migrations live in `adminme/migrations/`. Each is a Python file with a single `MIGRATION = Migration(...)` declaration.

Every schema change in v1.x that requires migration creates a migration file. v1.0 will have `0001_initial.py` establishing the baseline.

### `deploy/deploy.sh`

Operator-facing deploy script for the Mac Mini:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Pull latest main
cd "$ADMINME_CODE_DIR"
git pull origin main

# Verify git is clean
git status --porcelain && { echo "Uncommitted changes"; exit 1; }

# Stop services (graceful)
adminme service stop

# Run migrations (dry-run first)
adminme migration dry-run
read -p "Apply migrations? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || exit 1
adminme migration apply

# Restart services
adminme service start
adminme service status

# Smoke test
adminme instance status
```

And `deploy/rollback.sh` — reverses the last deploy (rolls back migrations, git checkouts previous HEAD, restarts services).

### Tests

Per subcommand group, unit tests for argument parsing + happy path.

Integration tests:
- `tests/integration/test_cli_end_to_end.py` — spin up lab instance, run several CLI commands, verify effects.
- `tests/integration/test_migration_framework.py` — apply a fake migration, verify state, roll back, verify rollback.
- `tests/integration/test_deploy_flow.py` — simulate a deploy (in a tmpdir, not real git), verify services restart and migrations apply.

## Verification

```bash
# Install CLI as entry point
poetry install
adminme --help
adminme instance --help
adminme event --help
# ... etc.

# Run each subcommand group's help
for grp in instance event projection skill pipeline plaid reminders member pack service cost observation xlsx migration openclaw deploy; do
  echo "=== $grp ==="
  adminme $grp --help
done

# Tests
poetry run pytest tests/integration/test_cli_end_to_end.py tests/integration/test_migration_framework.py tests/integration/test_deploy_flow.py -v

git add adminme/cli/ adminme/lib/migrations.py adminme/migrations/ deploy/ tests/
git commit -m "phase 17: CLI + deploy + migrations"
```

## Stop

**Explicit stop message:**

> Operator surface is live. `adminme` CLI works; deploy script pulls and applies; migrations safe. Ready for prompt 18 (integration test).
