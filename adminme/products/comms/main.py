"""
Comms product — Unified Communications FastAPI service at loopback :3334.

Implemented in prompt 13a per ADMINISTRATEME_BUILD.md §L5-continued and
architecture-summary.md §9.

Owns inbox aggregation, propose/commit outbound, approval queue, per-member-
per-channel access, batch windows.

Routers: /api/comms/{inbox, draft-queue, approve, send, channels, health,
interactions/:party_id, ingest/conversation-turn}.

The openclaw-memory-bridge plugin POSTs to
`/api/comms/ingest/conversation-turn` (DECISIONS.md §D6) — loopback-only,
authenticated with a shared secret written to
`config/plugin-secrets.yaml.enc` (path resolved via InstanceConfig) during
bootstrap §8. The plugin does NOT open a SQLCipher connection and does NOT
hold the AdministrateMe master key.

No scheduled jobs — all work is event-driven; adapters poll on their own
schedules and emit events; pipelines react (SYSTEM_INVARIANTS.md §7).

Binds to LOOPBACK only (§9 invariant 1).

Do not implement in this scaffolding prompt. Prompt 13a will fill in.
"""
