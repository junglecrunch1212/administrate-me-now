/**
 * AdministrateMe Node console — Express server at :3330.
 *
 * Implemented in prompt 14a per ADMINISTRATEME_BUILD.md §L5, SYSTEM_INVARIANTS.md §9,
 * and ADMINISTRATEME_CONSOLE_PATTERNS.md (12 patterns).
 *
 * This is the ONLY tailnet-facing surface on the host (§9 invariant 1).
 * Python product APIs (:3333–:3336) bind to loopback. Primary auth is the
 * Tailscale-User-Login header resolved via the party_tailscale_binding
 * projection (§9 invariant 5).
 *
 * The console NEVER reads the event log directly (§9 invariant 2) and NEVER
 * writes projection SQLite directly (§9 invariant 3). Writes proxy to the
 * Python product APIs through the HTTP bridge with tenant-header injection
 * and correlation-ID propagation.
 *
 * Key modules (filled in by prompt 14a/b/c):
 * - lib/session.js — authMember + viewMember (CONSOLE_PATTERNS.md §2)
 * - lib/bridge.js — HTTP bridge to Python APIs (§10)
 * - lib/guardedWrite.js — three-layer gate (§3): allowlist → governance → rate-limit
 * - lib/privacy_filter.js — calendar read-time redaction (§6)
 * - lib/nav.js — HIDDEN_FOR_CHILD (§7)
 * - lib/observation.js — final-outbound-filter (§11)
 * - routes/* — today, inbox, crm, capture, finance, calendar, scoreboard, settings, chat
 */

// Stub for now. Prompt 14a will fill in.
