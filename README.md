# AdministrateMe

AdministrateMe is a private household chief-of-staff system built on OpenClaw.
It keeps one encrypted event history, derives thirteen household views, and
gives each member only the information and actions their role permits.

## Start here: reviewed Mac installation

If you will run the household Mac, do not improvise from source files. Use only
a reviewed commit merged to `main`, then follow
[`docs/operations/FIRST-MAC-BOOTSTRAP.md`](docs/operations/FIRST-MAC-BOOTSTRAP.md)
from top to bottom. The family Mac is a deployment machine, never an
experimentation machine.

Before touching the family Mac, a reviewer should prove the credential-free
install on a disposable Apple-silicon Mac:

```bash
./bootstrap/install.sh --dry-run --fixture two-adult-one-child \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
./bootstrap/doctor.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

Expected result: JSON with `"status": "healthy"`, observation mode active,
and no live-provider claim. This rehearsal uses fake `.invalid` identities and
does not contact a household account.

For the later reviewed installation, the stable operator commands are:

```bash
./bootstrap/install.sh
./bootstrap/status.sh
./bootstrap/doctor.sh
./bootstrap/backup.sh
./bootstrap/stop.sh
./bootstrap/start.sh
```

Never paste an API key, Gateway token, password, OAuth code, or provider
credential into the member portal, a browser form, a GitHub issue, a support
bundle, or this repository. Provider credentials are configured server-side in
OpenClaw; AdministrateMe encryption and backup keys live in macOS Keychain.

## What is included

- One Python runtime and one native OpenClaw plugin; OpenClaw owns assistant
  chat, conversational memory, model routing, retries, and scheduling.
- Six product functions, thirteen projections, six reactive pipelines, two
  bidirectional workbooks, five profiles, four personas, and five adapter
  categories.
- An adult member portal with isolated OpenClaw chat, child/shared-screen
  restrictions, and private temporary quest views that cannot execute code or
  change household truth merely by changing a view.
- Rerunnable macOS bootstrap, Doctor, start/stop/status, encrypted backup,
  authenticated restore, guarded upgrade, and rollback.

## Credential-free developer demonstration

From a locked development checkout:

```bash
PYTHONPATH=bridge poetry run python -m adminme.cli.main demo canonical-message
```

The demo starts a temporary encrypted production composition, sends a
credential-free message through the real communications route, produces a
private proposal for independent approval, and exits. It opens no provider
connection.

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
