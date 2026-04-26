# ADR 0001 — Use OpenClaw as the assistant substrate

**Status:** ACCEPTED (by fiat in ADMINISTRATEME_BUILD.md §OPENCLAW IS THE
ASSISTANT SUBSTRATE; formalized here during phase 02 scaffolding).

**Date:** 2026-04-23.

**Deciders:** Operator (James), consistent with the build spec.

## Context

AdministrateMe is an event-sourced household chief-of-staff platform. To be
useful it needs:

- An assistant runtime that connects to text channels (iMessage via
  BlueBubbles, Telegram, Discord, web) and handles the agent loop, session
  management, and channel transport.
- A skill execution substrate — a way to invoke typed LLM-backed skills with
  schema validation, session scoping, and approval gates.
- A slash-command dispatcher — a way to let household members type verbs
  (`/digest`, `/whatnow`, `/bill`, `/approve`, etc.) and route them to code.
- A standing-orders mechanism — a way to register proactive behaviors
  (morning digest, paralysis detection, reminder dispatch) that share
  approval gating, observation-mode, and rate-limit machinery with
  interactive turns.
- An approval-gates system for any tool that actually touches the host.

Building all of this from scratch is a multi-year effort. OpenClaw already
provides every piece of the list above as a mature, documented, production-
ready daemon running on loopback `:18789`
(see `docs/openclaw-cheatsheet.md`).

## Decision

**AdministrateMe is layered on top of OpenClaw as the substrate.** OpenClaw
owns the agent loop, channel plugins, skill runner, slash dispatcher, session
manager, standing orders, cron, hooks, and approval gates. AdministrateMe
owns the event log (L2), 11 projections (L3), 17 pipelines (L4), L1 data
adapters, and the L5 surfaces (Node console at `:3330`, four Python product
APIs at `:3333`–`:3336`).

The two systems meet at exactly four documented seams
(SYSTEM_INVARIANTS.md §8 invariant 1; architecture-summary.md §2):

1. **Skills** — AdministrateMe's skill packs (classify_commitment_candidate,
   extract_commitment_fields, compose_morning_digest, etc.) install into
   OpenClaw via `openclaw skill install` or ClawHub. Pipelines invoke skills
   via `POST http://127.0.0.1:18789/tools/invoke` with `tool: "llm-task"` —
   `httpx` only; no provider SDK (SYSTEM_INVARIANTS.md §7 invariant 4 / §8
   invariant 2 / DECISIONS.md §D6; the manifest-to-args translation contract
   is specified in ADR-0002, which refines this seam without changing the
   ADR-0001 decision to use OpenClaw as substrate).
2. **Slash commands** — AdministrateMe registers household-CoS verbs with
   OpenClaw's slash dispatcher; handlers are AdministrateMe HTTP endpoints
   inside the Python product APIs (SYSTEM_INVARIANTS.md §8 invariant 6).
3. **Standing orders** — AdministrateMe registers proactive rules as
   OpenClaw standing orders. Per DECISIONS.md §D1 and
   `docs/openclaw-cheatsheet.md Q3`, OpenClaw's standing-orders mechanism is
   workspace prose in `AGENTS.md` paired with `openclaw cron add` cron jobs
   — NOT a typed registration API. AdministrateMe ships
   `bootstrap/openclaw/programs/` (one markdown per standing order) +
   `bootstrap/openclaw/cron.yaml`; bootstrap §8 concatenates these into
   `~/Chief/AGENTS.md` and issues `openclaw cron add` per the sidecar spec.
4. **Channels** — the `openclaw-memory-bridge` plugin ingests OpenClaw
   conversation state into the AdministrateMe event log as
   `messaging.received` and `conversation.turn.recorded` (one-way:
   SYSTEM_INVARIANTS.md §8 invariant 4). Per DECISIONS.md §D6, the plugin
   emits via `POST http://127.0.0.1:3334/api/comms/ingest/conversation-turn`
   (Comms product, loopback, shared-secret auth) — it does NOT open a
   SQLCipher connection and does NOT hold the AdministrateMe master key.
   Outbound channel sends go through OpenClaw's channel plugins; AdministrateMe
   does not open transports directly (SYSTEM_INVARIANTS.md §8 invariant 5).

**State boundary.** OpenClaw's memory stays in `~/.openclaw/`;
AdministrateMe's event log stays in the instance directory (path resolved
via InstanceConfig per SYSTEM_INVARIANTS.md §15 / DECISIONS.md §D15). The
two systems NEVER share a database and there is no shared SQLite file or
symlink (SYSTEM_INVARIANTS.md §8 invariant 8).

**Two independent gates.** OpenClaw's exec-approvals run at the tool-
execution boundary on the host after tool policy and before exec (cheatsheet
Q7). AdministrateMe's `guardedWrite` runs at the HTTP API boundary inside
the Node console before any write reaches a Python product API
(CONSOLE_PATTERNS.md §3, SYSTEM_INVARIANTS.md §6 invariants 5–8). Both must
pass; neither substitutes for the other (SYSTEM_INVARIANTS.md §8 invariant 7).

## Alternatives considered

### 1. Build a custom assistant runtime

Write AdministrateMe's own agent loop, channel plugins, skill runner, slash
dispatcher, and approval gates.

**Rejected.** Duplicates OpenClaw's work. OpenClaw is documented, battle-
tested, and actively maintained. Building a competing runtime would cost
months of engineering and introduce new bugs in well-understood territory.

### 2. Use OpenClaw only as a data layer

Use OpenClaw's memory-core as AdministrateMe's storage; do everything else
in AdministrateMe.

**Rejected.** OpenClaw's memory is conversation-centric, not event-sourced.
It does not provide the append-only log + projections + replay semantics
AdministrateMe needs (SYSTEM_INVARIANTS.md §1, §2). Coupling our data layer
to OpenClaw's would leak OpenClaw's model into every projection and break
the invariant that projections are pure functions of the event log.

### 3. Embed OpenClaw as a library

Statically link OpenClaw into AdministrateMe's processes.

**Rejected.** OpenClaw is a daemon; it owns a gateway socket (`:18789`) and
lifecycle. Embedding would require vendoring upstream and fighting its
process model. The loopback HTTP boundary is the cleanest seam.

## Consequences

**Positive.**

- AdministrateMe leverages a mature substrate for channels, sessions,
  skills, slash, standing orders, and exec-approvals.
- Provider routing (Anthropic, OpenAI, Ollama), retries, token accounting,
  and cache policy all live in OpenClaw — AdministrateMe does not touch
  provider SDKs (SYSTEM_INVARIANTS.md §7 invariants 4, 9; §8 invariant 2;
  DECISIONS.md §D6). `pyproject.toml` deliberately omits `anthropic` and
  `openai`.
- Household members interact with AdministrateMe through iMessage / Telegram
  / Discord / web out of the box — no bespoke channel work.

**Negative.**

- The OpenClaw ↔ AdministrateMe integration seam must stay in sync. When
  OpenClaw's docs change in a way that affects an AdministrateMe invariant,
  a dedicated phase prompt updates both the mirror and the affected
  invariants (DECISIONS.md §D12).
- Two session/memory models must be bridged — OpenClaw's session and
  AdministrateMe's L3 projections. The `openclaw-memory-bridge` plugin is
  the one-way ingest path; AdministrateMe-originated context does not read
  back into OpenClaw's memory (SYSTEM_INVARIANTS.md §8 invariant 4).
- Proactive behaviors register as OpenClaw standing orders (workspace prose
  + cron), not as typed handlers, so the registration path is by generated
  markdown — more care needed in prose authoring; no compile-time contract.

## References

- ADMINISTRATEME_BUILD.md §OPENCLAW IS THE ASSISTANT SUBSTRATE — the
  constitutional source of this decision.
- `docs/SYSTEM_INVARIANTS.md` §2 (projections are derived), §7 (pipelines),
  §8 (OpenClaw boundaries), §14 (scheduling boundary).
- `docs/DECISIONS.md` §D1 (standing orders registration), §D6 (memory-
  bridge HTTP seam).
- `docs/architecture-summary.md` §2 (how OpenClaw fits).
- `docs/openclaw-cheatsheet.md` (8 Q&As on OpenClaw's surfaces).
