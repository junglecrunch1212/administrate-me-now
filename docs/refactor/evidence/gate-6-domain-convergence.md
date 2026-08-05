# Gate 6 domain convergence evidence

Date: 2026-08-03

Branch: `codex/administrateme-v2-runtime`

Draft PR: <https://github.com/junglecrunch1212/administrate-me-now/pull/248>

## Result

Gate 6 passes at the credential-free, deterministic-fixture, production-
composition boundary. Gates 1–5 were verified from committed evidence before
the Phase 6 work. No live Gmail, Apple, Plaid, Home Assistant, OAuth,
Tailscale, or family-Mac success is claimed.

The final inventory contains 645 relevant first-party Python/type-stub,
TypeScript/JavaScript, SQL, shell, YAML, JSON, and package-manifest files. The
runtime exposes six product namespaces, exactly thirteen projection
capabilities, six reactive pipelines, five OpenClaw-scheduled proactive
programs, ten skill packs, five profile packs, four persona packs, two
bidirectional workbooks, and the Cat-A–E adapter taxonomy.

## Executable evidence

| Contract | Evidence |
|---|---|
| Full Python behavior | `PYTHONPATH=.;bridge poetry run pytest -q` → 1,270 passed, 3 skipped. The skips are platform/live-provider guards, not unexpected xfails. |
| Acceptance slices | `PYTHONPATH=.;bridge poetry run pytest -q tests/acceptance` → 12 passed. |
| Independent bridge package | `PYTHONPATH=.;bridge poetry run pytest -q bridge/tests` → 60 passed. |
| Notification-producing proactive pipelines | Morning digest, CRM surface, custody brief, and reminder focused unit/integration set → 38 passed; no fake `external.sent`. |
| Reactive redelivery | Commitment, identity, and thank-you focused set → 42 passed; deterministic IDs and causation-based duplicate rejection. |
| Workbooks | Ops/finance builders, sidecars, reverse diff, lock/conflict, and round-trip focused set → 28 passed. |
| OpenClaw | `npm run check`, contract validation, 11 Vitest tests, TypeScript build, workspace compile for six programs/persona, and `npm pack --dry-run` pass. |
| Formatting/lint/types | Ruff format/check pass across 413 Python files; mypy passes 172 source files; Pyright reports 0 errors; TypeScript `tsc --noEmit` passes. |
| Packages/dependencies | Root and bridge Poetry checks/builds pass; root lock regenerated; npm production audit reports 0 vulnerabilities. Removed dependencies have no live first-party imports. |
| Repository invariants | `scripts/verify_invariants.sh`, `git diff --check`, schema/pack validators, and tenant/path scans pass. |

## Mandatory-contract mapping

- Adapter category, real-sink, capability, sharing, bridge-scope, and write-
  capability matrices are covered by the adapter framework, dual-deployment,
  pack validator, bridge, and runtime acceptance suites.
- All thirteen projections are registered by one composition root; replay,
  rebuild, cursor resume, idempotent apply, schema migration, scope, and
  encrypted reopen are covered by the complete full suite and the acceptance
  runtime/knowledge slices.
- `member_knowledge` tests cross the real bridge HTTP ingest, encrypted log,
  bus, projection, correction, replay, and vector handoff. Privileged and
  cross-owner embedding/read attempts are denied.
- Workbook tests cover both named files, debounce, atomic replacement, locks,
  sidecars, protected/derived columns, reverse events, conflicts, Plaid
  authority, and rebuild from event history.
- The event/schema/projection tests preserve semantic separation among event
  facts, calendar events, list items, tasks, commitments, recurrences, and
  scheduler jobs.
- Profile/persona tests prove discovery, tenant-neutral validation, stage,
  test, activation, journal recovery, and rollback. Persona identity compiles
  into OpenClaw; profile behavior feeds member-facing ranking/policy without
  changing authorization or model routing.
- Cursor-store tests cover watermark, lease/claim, deduplication, error count,
  circuit opening, recovery, and restart. Notification tests cover
  suppression, approval holds, quiet/not-before retry, routing, delivery
  circuit, idempotent result ingestion, and cross-owner denial without sending
  a message.

## Inventory and disposition

KEEP: the encrypted event runtime, thirteen projections, two workbooks, six
product namespaces, scoped domain/member knowledge, Cat-A–E contracts,
provider fixtures, reactive pipelines, OpenClaw standing orders, skills,
profiles, personas, governance/observation, bootstrap/operations, and the
native plugin.

DEFER: live optional-provider credentials/activation, optional CalDAV/Drive/
channels/providers, adult-only Obsidian per member, and the full browser
household UX. Deferred items are disabled or absent from ready capability
state; their constitutional domain/API/workbook behavior remains.

DELETE: the empty Node console runtime/package, prose-only pipeline and xlsx
daemon duplicates, dead correlation/identifier helpers, AdministrateMe-owned
scheduler dependencies/config, speculative no-op sends and fake bridge target
IDs, and unused platform dependencies.

## Human action

Before family activation, a maintainer must review and merge the draft PR and
run the documented disposable Apple-silicon rehearsal, including FileVault,
Keychain, launchd/login recovery, Apple grants, Tailscale identity, the exact
pinned OpenClaw live test, and only the providers intentionally configured.
