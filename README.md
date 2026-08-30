# AdministrateMe

AdministrateMe is a private household chief-of-staff system built on OpenClaw.
It keeps one encrypted event history, derives thirteen household views, and
gives each member only the information and actions their role permits.

## Start here: reviewed Mac installation

If you will run the household Mac, do not improvise from source files. Use only
a reviewed commit merged to `main`, then follow the parallel wizard read-along
in [`docs/operations/MAC-MINI-SETUP-READ-ALONG.md`](docs/operations/MAC-MINI-SETUP-READ-ALONG.md)
from top to bottom. The family Mac is a deployment machine, never an
experimentation machine.

Before touching the family Mac, a reviewer should prove the credential-free
install on a disposable Apple-silicon Mac:

```bash
./bootstrap/install.sh --fixture two-adult-one-child \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab" \
  --expected-commit "$APPROVED_SHA"
./bootstrap/doctor.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

`--dry-run` is reserved for the isolated test harness and is refused in an
operator installation. Expected result: JSON with `"status": "healthy"`,
observation mode active, and no live-provider claim. This rehearsal uses fake `.invalid` identities and
does not contact a household account.

For the later reviewed installation, the stable operator commands are:

```bash
./bootstrap/install.sh --expected-commit "$APPROVED_SHA"
./bootstrap/status.sh
./bootstrap/doctor.sh
./bootstrap/backup.sh
./bootstrap/stop.sh
./bootstrap/start.sh
./bootstrap/activate-openclaw.sh
./bootstrap/bridge-status.sh
```

Never paste an API key, Gateway token, password, OAuth code, or provider
credential into the member portal, a browser form, a GitHub issue, a support
bundle, or this repository. Provider credentials are configured server-side in
OpenClaw; AdministrateMe encryption and backup keys live in macOS Keychain.

## What is included

- One Python runtime and one native OpenClaw plugin; OpenClaw owns assistant
  chat, conversational memory, model routing, retries, and scheduling.
- Six product functions, thirteen domain projections plus derived Triage, six reactive pipelines, two
  bidirectional workbooks, five profiles, four personas, and five adapter
  categories.
- An adult member portal with isolated OpenClaw chat, child/shared-screen
  restrictions, and private temporary quest views that cannot execute code or
  change household truth merely by changing a view.
- Rerunnable macOS bootstrap, Doctor, start/stop/status, encrypted backup,
  authenticated restore, guarded upgrade, rollback, exact-source OpenClaw
  provenance, owned cron reconciliation, and revocable per-member bridge
  enrollment.

## Credential-free developer demonstration

From a locked development checkout:

```bash
PYTHONPATH=bridge poetry run python -m adminme.cli.main demo canonical-message
```

The demo starts a temporary encrypted production composition, sends a
credential-free message through the real communications route, produces a
private proposal for independent approval, and exits. It opens no provider
connection.

For continuous human review of the complete member portal, use a dedicated
fictional instance directory:

```bash
PYTHONPATH=bridge poetry run adminme demo synthetic-member \
  --instance-dir /absolute/path/to/dedicated-synthetic-instance
```

Open `http://127.0.0.1:8321/member/triage`. This persistent sandbox contains
only fictional members, accounts, source content, calendar/tasks, finance,
Triage history, feedback, and rules. It uses the encrypted production event
log and normal projections/pipelines, but never loads provider adapters. The
default deterministic OpenClaw transport is repeatable and cost-free. An
explicit `--model-transport gateway` uses only the pinned loopback Gateway and
requires the normal OpenClaw token and shared-secret environment variables.

## Verification and architecture

The release aggregate on supported macOS is:

```bash
bash scripts/acceptance/release-candidate.sh
```

The executable contract and final evidence are in
[`docs/refactor/ACCEPTANCE-CONTRACT.md`](docs/refactor/ACCEPTANCE-CONTRACT.md)
and
[`docs/refactor/FINAL-VALIDATION-REPORT.md`](docs/refactor/FINAL-VALIDATION-REPORT.md).
The governing architecture remains in `AGENTS.md`, `docs/SYSTEM_INVARIANTS.md`,
`docs/DECISIONS.md`, and `docs/refactor/ADR/`.

Live Gmail, Apple, Plaid, Home Assistant, Tailscale, model-provider, and macOS
privacy grants are intentionally excluded from credential-free test evidence.
Their exact post-merge activation boundary is documented in the first-Mac
guide and [`KNOWN-LIMITATIONS.md`](KNOWN-LIMITATIONS.md).
