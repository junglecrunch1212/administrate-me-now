# ADR-0006 — Runtime baseline and reviewed cutover

Status: **ACCEPTED; LIVE CUTOVER AUTHORIZED, KEYCHAIN-BRIDGED RETRY PENDING**

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

macOS Keychain items retain both an application allowlist and an application
partition bound to the creating interpreter. A login-Keychain password reset or
an ACL edit that appears to allow two Python applications can therefore still
strand the values at the partition boundary. The migration must not reset the
Keychain, select `Allow all applications`, or require a household password.

For an approved Python-boundary migration, the reviewed launcher runs a small
coordinator under the active interpreter. It reads each currently effective
item in process memory and sends the value only through an anonymous pipe to a
bounded candidate-interpreter child. The child creates and reads back a new,
runtime-bound item. After every item verifies, the coordinator atomically
publishes a non-secret logical-reference alias map. The candidate store resolves
logical references through that map; an older rollback release ignores the map
and continues using its untouched original items. A later retry compares the
alias runtime tag with the active release receipt, so it also reads those
originals instead of prompting the rolled-back interpreter for a newer
runtime's copies. A missing source, failed
copy, invalid map, prompt, timeout, or failed candidate probe aborts before
release preparation or service mutation. Secret values never cross command
arguments, environment variables, files, logs, or diagnostic output;
subprocess standard I/O remains captured in anonymous pipes.

Candidate preflight enumerates configured logical references only. The
generated alias map is resolution metadata, not another source of configured
credentials, so physical targets are verified through logical resolution in
the production store and are never probed as rollback-keyring originals.

Runtime-tag comparison accepts the current receipt's exact
`python.preferred` value and the older D10 receipt's strict
`python@major.minor` formula when `preferred` is absent. It does not infer a
runtime from an open-ended compatibility range or an ambiguous executable
name. This preserves rollback from reviewed legacy releases without weakening
the receipt boundary.

An in-process candidate read-back is not launchd evidence for an ad-hoc-signed
Homebrew interpreter. Runtime-bound alias items are therefore created, read,
updated, deleted, and independently verified through Apple's signed
`/usr/bin/security` client. Secret values use anonymous standard-I/O pipes only;
`-w` is passed without a value, and stdout/stderr are captured and never
included in diagnostics. Untouched rollback originals continue through their
own interpreter's Python keyring path. Adapter credential reads resolve the
same validated alias map before choosing the signed-client path. This is a
narrow Keychain transport change, not an additional secret store or a runtime
process.

Apple's prompt-mode `-w` reader also truncates long input. Every signed-client
value is therefore stored as a versioned, checksummed envelope: Base64
fragments and the root manifest are individually bounded below the prompt
limit, fragments use a random generation, and the manifest is published last.
A reader returns plaintext only after strict manifest validation, complete
reassembly, UTF-8 decoding, size enforcement, and digest verification. Failed
writes clean their unpublished generation; successful replacement may clean
the prior generation only after the new root is committed. This fragmentation
is an implementation detail inside the same Keychain store, not a new source
of truth or a relaxation of the anonymous-pipe rule.

Rollback may cross the version boundary because it restores a previously
reviewed immutable release and its own venv. Stable wrappers resolve the active
release first, then select both its interpreter and the Node formula declared
by its own `bootstrap/versions.json`; a candidate checkout cannot leak its
runtime path into an older active release. The wrappers retain a 3.11 parser
fallback only to read an older release pointer during migration or rollback;
that fallback is not a future application-runtime target.

Launch sequencing probes the lightweight `/api/system/live` endpoint before
starting OpenClaw. The liveness wait is bounded at five minutes because the
first start of a reviewed schema/projection release can perform cold migration
and replay work; the prior 90-second bound rolled back a process that completed
startup immediately afterward. Full `/api/system/ready` capability diagnostics
remain the health/status contract and the post-switch Doctor remains
authoritative. This prevents cold replay or a busy provider refresh from being
misclassified as a dead Python process without weakening any readiness or
release gate.

## Consequences

- Root, bridge, pack, CI, bootstrap, Doctor, and plugin manifests agree on the
  target runtime.
- Poetry remains outside the environment it synchronizes and uses `poetry
  sync`, eliminating the self-deletion failure mode.
- Node 24 can be installed without relinking Node 22, so the live Gateway and
  runtime are unchanged while the candidate is tested.
- D10 remains historical evidence and is superseded by D30.
- The 3.11/22 live instance remains healthy until the separately authorized
  migration completes; its original Keychain items remain a rollback asset.

## Classification

- **PRESERVE:** topology, encrypted truth, exact OpenClaw pin, SQLCipher
  boundary, release-local environments, backup, rollback, and observation.
- **MODERNIZE:** Python, Node, npm, Poetry, manifests, CI, and installer paths.
- **SUPERSEDE:** D10 as the future runtime baseline and implicit same-runtime
  upgrade assumptions.
- **DEFER:** only completion of the already authorized live cutover; the
  candidate architecture, rehearsal, and executable migration gate are
  complete.
