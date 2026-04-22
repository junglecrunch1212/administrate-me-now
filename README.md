# AdministrateMe

A household chief-of-staff platform built on OpenClaw. Event-sourced, projection-based, multi-member, privacy-aware.

## If you're the operator (James)

Start with **`ADMINISTRATEME_FIELD_MANUAL.md`** — the non-coder's guide to running this system. Chapter 1 is the most important.

When you're ready to start the build, open `prompts/PROMPT_SEQUENCE.md` and follow it.

## If you're Claude Code about to execute a prompt

Read the universal preamble at the bottom of `prompts/PROMPT_SEQUENCE.md` first. Then read the specific prompt you've been handed. Then do the work.

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

🟡 In progress — Phase A build prompts ready; code generation not yet started.
