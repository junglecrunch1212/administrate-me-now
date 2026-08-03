# AdministrateMe

A household chief-of-staff platform built on OpenClaw. Event-sourced, projection-based, multi-member, privacy-aware.

## Mac mini bootstrap and operations

Gate 5 provides stable, rerunnable operator commands under `bootstrap/` for
install, Doctor, start, stop, status, encrypted backup, and rollback-safe
restore. The exact credential-free Phase-A rehearsal is:

```bash
./bootstrap/install.sh --dry-run --fixture two-adult-one-child --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

This does not activate the future family Mac or claim any live provider.
Read `docs/refactor/MAC-MINI-OPERATIONS.md` before using the package.

## Credential-free canonical demo

From the repository root, run:

```powershell
$env:PYTHONPATH = "bridge"
python -m adminme.cli.main demo canonical-message
```

This starts a temporary encrypted one-process instance, invokes the real versioned `POST /api/comms/v1/demo/canonical-message` product route, and exits after printing JSON. It uses only `.invalid` identifiers, the `fixture-messaging` Cat-A adapter, and the deterministic `fixture/deterministic-v1` OpenClaw `llm-task` transport. It reads no credential and opens no network connection.

The default golden input asks the configured synthetic principal to reply to `sender@fixture.invalid`. Expected output has `state: "proposal_only"`, one `source_event_id`, its deterministic `ix_<source_event_id>` interaction, and one `cmt_<hash>` proposal. Approval is deliberately a separate principal action. See `docs/refactor/evidence/gate-3-household-slice.md` for the API/lifecycle contract and adversarial matrix.

## Native OpenClaw seam

Gate 4 adds the single plugin package at `openclaw/administrateme/`, pinned to
OpenClaw source version `2026.7.2` and commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`. It contains typed tools,
dashboard bindings, slash commands, an OpenClaw-format skill, standing-order
automation declarations, explicit conversation hooks, and the compiled
instance SOUL workspace. The authenticated loopback API still resolves and
authorizes household members inside AdministrateMe; an OpenClaw operator token
or approval cannot bypass it.

See `docs/refactor/OPENCLAW-COMPATIBILITY.md` for exact fixture/live evidence.
The contract fixture and real AdministrateMe process gate run on this Windows
development host. Live OpenClaw installation remains deliberately unclaimed
until `npm run test:live:mac` passes on a disposable macOS lab with a Node
version satisfying the exact source engine and the exact source checkout.

## If you're the operator (James)

Start with **`ADMINISTRATEME_FIELD_MANUAL.md`** — the non-coder's guide to running this system. Chapter 1 is the most important.

When you're ready to start the build, open `prompts/PROMPT_SEQUENCE.md` and follow it.

## If you're Claude Code about to execute a prompt

Read the universal preamble at the bottom of `prompts/PROMPT_SEQUENCE.md` first. Then read the specific prompt you've been handed. Then do the work.

## Constitutional reference (read first, cite in every commit)

Every phase prompt (02–19) treats the files below as binding. If code being
written would violate one, stop and flag it.

- [`docs/SYSTEM_INVARIANTS.md`](docs/SYSTEM_INVARIANTS.md) — 15 numbered
  sections of cross-cutting invariants + 8 proposed invariants resolved in
  DECISIONS.md. Cite as `[§N]`.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — append-only resolutions to
  SYSTEM_INVARIANTS.md §16 ambiguities + additional decisions (Hearth,
  platform versions, skill-pack shape, doc versioning). Cite as `[DN]`.
- [`docs/architecture-summary.md`](docs/architecture-summary.md) — the
  five-layer model, OpenClaw fit, event log, 11 projections, 17 pipelines,
  security + privacy, 6 pack kinds, console, Python product APIs, bootstrap.
  Cite as `[arch §N]`.
- [`docs/openclaw-cheatsheet.md`](docs/openclaw-cheatsheet.md) — 8 Q&As on
  OpenClaw's skill / slash / standing-order / plugin surfaces. Cite as
  `[cheatsheet Qn]`.
- [`docs/reference/_manifest.yaml`](docs/reference/_manifest.yaml) — git-
  commit-pinned manifest of mirrored external docs (OpenClaw, Plaid,
  BlueBubbles, Google APIs, Textual, SQLCipher, etc.). Cite external docs
  from the local mirror under `docs/reference/<section>/`, never via
  WebFetch.

## The documents in this repo

### Specification (read as reference material)

- **`ADMINISTRATEME_BUILD.md`** — the canonical build specification (3,500+ lines). Everything you build has authority here.
- **`ADMINISTRATEME_CONSOLE_PATTERNS.md`** — 12 patterns for the Node console layer.
- **`ADMINISTRATEME_CONSOLE_REFERENCE.html`** — interactive visual/interaction design reference.
- **`ADMINISTRATEME_DIAGRAMS.md`** — 10 architectural diagrams with ASCII art.
- **`ADMINISTRATEME_REFERENCE_EXAMPLES.md`** — 7 worked examples (adapter, pipeline, skill pack, projection, event, profile, persona).

### Operator guide

- **`ADMINISTRATEME_FIELD_MANUAL.md`** — 10-chapter guide for running this system as a non-coder with a family to serve.

### Build prompts

- **`prompts/PROMPT_SEQUENCE.md`** — overview, dependency graph, universal preamble.
- **`prompts/00-*` through `prompts/19-*`** — 33 prompts covering every phase of the build.
- **`prompts/diagnostics/d01-*` through `d08-*`** — 8 failure-mode recovery prompts.

## The two-phase build model

**Phase A** (Claude Code, this repo): generate all application code, the bootstrap wizard, the CLI, the mirrored external documentation. Claude Code works entirely within Anthropic's sandbox against this GitHub repo. No Mac Mini. No live OpenClaw. No real credentials.

**Phase B** (operator, the Mac Mini): install OpenClaw + BlueBubbles + Tailscale + 1Password CLI on the Mac Mini. Clone this repo. Run `./bootstrap/install.sh`. Wizard walks you through nine sections. At the end, the instance is live.

See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 for Phase B setup.

## Timeline estimate

- Phase A: ~93-123 hours of Claude Code work, spread across ~2-4 weeks of Saturday sessions.
- Phase B: ~2-4 hours once all credentials are at hand.
- Observation period after Phase B: 7 days before the family actually uses the system.

## Status

In progress — the controlled v2 refactor has proven Gates 1–4 at their declared evidence levels. Gate 4 is the current native OpenClaw seam; Phase 5 is the member-owned private searchable-knowledge slice. The family Mac remains untouched pending reviewed merge and the disposable-lab live OpenClaw gate.
