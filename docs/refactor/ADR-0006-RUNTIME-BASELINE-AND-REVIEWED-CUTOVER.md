# ADR-0006 — Runtime baseline and reviewed cutover

Status: **ACCEPTED; CANDIDATE VALIDATED, LIVE CUTOVER NOT AUTHORIZED**

Date: 2026-08-07

Decision owners: household principals and AdministrateMe product/engineering

Governing change order: `CHANGE-ORDER-005-CONSTITUTIONAL-AUDIT.md`

## Context

D10 fixed the first implementation to Python 3.11 and Node 22. That was a
valid historical sandbox constraint, not a durable platform objective. The
current Mac already supports a side-by-side Python 3.14 toolchain; Node 24 is
the active LTS line supported by the pinned OpenClaw source. Homebrew 6,
Poetry 2.4, and npm 11 also supersede the installer assumptions that created
the original environment.

A dependency update is not permission to replace a healthy household runtime.
The current service owns an encrypted log, projections, adapter cursors,
Keychain references, OpenClaw state, and user LaunchAgents. Changing its
interpreter or JavaScript runtime therefore remains a release migration with
backup, clean installation, replay, Doctor, browser, rollback, and explicit
operator approval—not an opportunistic package-manager side effect.

## Decision

The target release baseline is:

- CPython `>=3.14,<3.15`, preferred `3.14.6`, from `python@3.14`;
- Node `>=24.15,<25`, preferred `24.19.0`, from `node@24`;
- Homebrew `>=6,<7`, Poetry `2.4.1`, and npm `>=11.6,<12`;
- SQLCipher `>=4.12,<5` through the existing `sqlcipher3==0.6.2` boundary;
- OpenClaw `2026.7.2` at reviewed commit
  `944cf08f94c334cff2b0fdf7e01187a9efa42715`, unchanged.

Python and Node are installed side by side. The candidate builds a new,
release-local virtual environment and plugin dependency tree. It never edits
the active release's venv, changes the global Node link, or restarts a service
during candidate preparation.

An upgrade compares the candidate manifest to the manifest of the release
named by `current-release.json`. A Python or Node baseline change fails before
service replacement unless `--approve-runtime-migration` is supplied. That
flag is valid only after the candidate passes the full release suite on 3.14/
24, a clean isolated bootstrap, encrypted backup/restore and projection replay,
the pinned OpenClaw build/registration tests, browser simulation, and a staged
rollback rehearsal. The actual family instance then still requires a separate
operator decision.

The explicit migration launcher requires a clean reviewed Git checkout and
resolves its commit before execution. Because the older active release cannot
self-host code that requires a newer interpreter, this path creates a separate
candidate-version controller venv under instance tooling, synchronizes it with
Poetry kept outside that venv, and invokes the candidate upgrade manager from
there. The ordinary same-runtime updater continues to execute the active
release's trusted controller. Neither path mutates the active application venv.

macOS Keychain items retain an application allowlist. Before release
preparation or service mutation, the migration controller probes every
Keychain reference present in validated instance JSON from the candidate
interpreter in bounded child processes. A prompt, timeout, or inaccessible item
fails closed while the old release remains active. The operator must keep
`Confirm before allowing access` and add only the reviewed Python 3.14
application alongside the existing Python entry for every AdministrateMe item;
`Allow all applications` is prohibited. Secret values never cross command
arguments or diagnostic output.

Rollback may cross the version boundary because it restores a previously
reviewed immutable release and its own venv. Stable wrappers resolve the active
release first, then select both its interpreter and the Node formula declared
by its own `bootstrap/versions.json`; a candidate checkout cannot leak its
runtime path into an older active release. The wrappers retain a 3.11 parser
fallback only to read an older release pointer during migration or rollback;
that fallback is not a future application-runtime target.

Launch sequencing probes the lightweight `/api/system/live` endpoint before
starting OpenClaw. Full `/api/system/ready` capability diagnostics remain the
health/status contract and the post-switch Doctor remains authoritative. This
prevents a busy provider refresh from being misclassified as a dead Python
process without weakening any readiness or release gate.

## Consequences

- Root, bridge, pack, CI, bootstrap, Doctor, and plugin manifests agree on the
  target runtime.
- Poetry remains outside the environment it synchronizes and uses `poetry
  sync`, eliminating the self-deletion failure mode.
- Node 24 can be installed without relinking Node 22, so the live Gateway and
  runtime are unchanged while the candidate is tested.
- D10 remains historical evidence and is superseded by D30.
- The 3.11/22 live instance is healthy but is not evidence that the 3.14/24
  candidate has been promoted.

## Classification

- **PRESERVE:** topology, encrypted truth, exact OpenClaw pin, SQLCipher
  boundary, release-local environments, backup, rollback, and observation.
- **MODERNIZE:** Python, Node, npm, Poetry, manifests, CI, and installer paths.
- **SUPERSEDE:** D10 as the future runtime baseline and implicit same-runtime
  upgrade assumptions.
- **DEFER:** only the live cutover, pending explicit post-review authorization;
  the candidate architecture and executable migration gate are not deferred.
