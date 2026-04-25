# Tier C memo: prompt 08 split — `08a` Session+scope / `08b` governance+observation

**Author:** Build Supervision Partner
**Date:** 2026-04-25
**Type:** Tier C split memo (no build code; sequence-update only)
**Resolves:** UT-3 (prompt 08 likely needs split into 08a / 08b)
**Authority:** `D-prompt-tier-and-pattern-index.md` pre-split disposition column = "Pre-split candidate — Tier C memo first."

---

## Why split

The current `prompts/08-session-scope-governance.md` (183 lines, unrefactored) specifies four new modules — `session.py`, `scope.py`, `governance.py`, `observation.py` — plus a sweeping integration step that wraps every existing projection query with the new Session API. Three independent forces push the prompt over Claude Code's per-session execution capacity:

1. **48 `# TODO(prompt-08)` markers across 10 projection query files** (parties 124L, commitments 116L, tasks 126L, vector_search 102L, calendars 177L, interactions 114L, money 166L, recurrences 92L, artifacts 57L, places_assets_accounts 131L — total ~1,205 lines of merged code that 08 modifies). The marker count is **higher than the index's previous estimate of ~38** — the 07a/07b/07c-α/07c-β cohort added more.
2. **Two distinct architectural concerns.** Sessions + scope (read-side defense-in-depth, query-time predicate composition, `ScopeViolation` exception, view-as machinery, privacy filter at read) is a different surface from governance + observation (write-side three-layer guardedWrite, action_gates, RateLimiter, hard-refuse list, observation final-outbound-filter wrapper, `observation.suppressed` event). Co-shipping them would require a single Claude Code session to hold the full §6 invariant set + §AUTHORITY config schema + §OBSERVATION wiring + the projection-wrap refactor — a known timeout pattern.
3. **Pattern-introduction risk.** Per `D-prompt-tier-and-pattern-index.md`, 08 is an **Introduction** (security backbone, four new modules). Introductions get their own dedicated refactor session; the security backbone deserves two.

The 07c saga is the precedent: an "Introduction" prompt with a heavy code body and a heavy projection-wrap pass will not fit one Claude Code session window.

## Proposed split

### Sub-prompt 08a — Session + scope enforcement (read side)

**Scope:** populate the `Session` + scope-filter machinery and wrap every projection query.

**Modules touched (populating existing docstring-only stubs):**
- `adminme/lib/session.py` (currently 21-line docstring) → full `Session` dataclass, three constructors (`build_session_from_node`, `build_session_from_openclaw`, `build_internal_session`), view-as validation, `allowed_scopes` derivation. Estimate: ~250-320 lines.
- `adminme/lib/scope.py` (currently 14-line docstring) → `ScopeViolation` exception, `allowed_read`, `privacy_filter` (privileged → busy redaction at read), `coach_column_strip`, `child_hidden_tag_filter`. Estimate: ~180-240 lines.

**Integration touch (the heavy work):**
- Add `session: Session` keyword param + scope predicate composition to **48 query functions across 10 files** in `adminme/projections/{parties,commitments,tasks,vector_search,calendars,interactions,money,recurrences,artifacts,places_assets_accounts}/queries.py`. Most of this is mechanical: each function gains a `session: Session` arg, the SQL gains a scope-WHERE clause, the rows pass through `privacy_filter`. Per-file delta is ~30-60 lines (an extra param + an extra WHERE clause + a filter call per query).
- Update each file's existing test file to use the new signature.

**Invariants in scope:** `[§6.1]` no global DB connection, `[§6.2]` authMember/viewMember split, `[§6.3]` writes always use authMember, `[§6.4]` scope predicates auto-appended, `[§6.9-12]` privileged read rules, `[§6.17-18]` HIDDEN_FOR_CHILD nav (server-side prefix blocklist deferred to 14a per `prompts/08`'s out-of-scope; this prompt names the blocklist as a constant only), `[§3.4]` member_id keying, `[§9.5]` Tailscale-User-Login resolution.

**Out of scope (08a):**
- guardedWrite write path (08b).
- ActionGate / RateLimiter / hard-refuse list (08b).
- Observation mode wrapper (08b).
- The Node console's parallel guardedWrite implementation (14a per prompt 08's existing out-of-scope clause).
- Server-side `CHILD_BLOCKED_API_PREFIXES` enforcement (14a — the constant lands in 08a, the middleware lands in 14a).

**Tests (08a):**
- `tests/unit/test_session.py` — view-as blocking matrix from `DIAGRAMS.md §4` (every cell in the 6-column auth/view-as table). ≤ ~250 lines.
- `tests/unit/test_scope.py` — read matrix from `DIAGRAMS.md §5` (every (auth_role × sensitivity × owner_scope) cell, ~24 cases). ≤ ~300 lines.
- One test per projection that currently has a TODO marker — assert `ScopeViolation` raised when a query is called with an out-of-scope `Session`. Adds ~10 small tests across the existing test files.

**Per-commit budget check (§2.9):**

| Commit | Production code | Test code | Files read |
|---|---|---|---|
| 1 | `session.py` populated (~280 lines) + `scope.py` populated (~210 lines) | `test_session.py` (~240 lines, 1 file) | 4: `BUILD.md §L3-CONT`, `SYSTEM_INVARIANTS.md §6`, `CONSOLE_PATTERNS.md §2`, `DIAGRAMS.md §4` |
| 2 | Wrap 5 projection query files (parties + interactions + artifacts + commitments + tasks ≈ 537 merged lines, +~150 delta) | `test_scope.py` (~300 lines, 1 file) | 5: each projection's `queries.py` + `DIAGRAMS.md §5` |
| 3 | Wrap remaining 5 projection query files (recurrences + calendars + places_assets_accounts + money + vector_search ≈ 668 merged lines, +~150 delta) | Existing test files updated for new signature (~80 lines added across ~10 files; counts as test edits, not net-new file count) | 5: each projection's `queries.py` |
| 4 | BUILD_LOG entry; `verify_invariants.sh` invocation; mypy preflight | — | 1: `qc_rubric.md` template |

**Sizing verdict for 08a:** comfortably within budgets. Total prompt target: ~270 lines / ~22 KB. Inline code: ~25-30 lines (Session dataclass shape; one example projection wrap; `ScopeViolation` definition). Per-commit code stays under 600-line budget; test files stay under 400 lines / 2-file budget per commit (note Commit 3 only edits existing test files, no new test files).

**Carry-forwards from 08a → 08b:**
- `Session` API surface frozen (08b consumes it).
- `ScopeViolation` exception lands in 08a; 08b reuses for write denials? **No** — write denials are `GuardedWriteResult.denied`, a separate path. 08a's exception is read-side only.
- `child_forbidden_tags` constant lives in 08a (referenced by 08b's `outbound` for child-session outbound suppression).

**Carry-forwards from 08a → prompt 08.5 (none expected) → prompt 09a:**
- 08a alone is shippable. The next prompt that consumes Session is 13a (FastAPI services that take Session per route), but 09a (skill runner) calls `Session.tenant_id` to scope skill calls. 08a's Session is enough for 09a.

**Closes UT-7?** **No.** UT-7 (reverse-daemon emit path bypasses Session/guardedWrite) requires the `outbound()` wrapper, which lives in 08b. UT-7 carries through 08a and closes when 08b ships.

---

### Sub-prompt 08b — Governance + observation mode (write side)

**Scope:** populate the guardedWrite three-layer machinery + the observation final-outbound-filter wrapper.

**Modules touched (populating existing docstring-only stubs):**
- `adminme/lib/governance.py` (currently 25-line docstring) → `ActionGateConfig` (loaded from `config/governance.yaml`), `RateLimiter` (sliding-window per `(tenant_id, scope, action)`), `AgentAllowlist`, `GuardedWrite.check()` returning `GuardedWriteResult`, `write.denied` event emission with `layer_failed` attribution. Estimate: ~350-440 lines.
- `adminme/lib/observation.py` (currently 23-line docstring) → `ObservationState`, `ObservationManager` (reads `config/runtime.yaml`, persists via `observation.enabled` / `observation.disabled` events), `outbound()` wrapper that emits `observation.suppressed` with full would-have-sent payload OR calls `action_fn` and emits `external.sent`. Estimate: ~220-300 lines.

**New event types registered at v1 per `[D7]`:**
- `write.denied` (governance attribution: `layer_failed` ∈ {`allowlist`, `governance`, `rate_limit`}, `reason`, original payload echo)
- `review_request` (held-for-review; emitted on `review` gate)
- `observation.suppressed` (full would-have-sent payload + `observation_mode_active`)
- `observation.enabled` / `observation.disabled` (state-change audit)
- `external.sent` (success-path companion to `observation.suppressed`)

(Five new event types. `ALLOWED_EMITS` in `scripts/verify_invariants.sh` does NOT need updating — these are emitted from product code / outbound wrappers, not from projections, so the projection-emit canary is unaffected. Memo to Partner reviewing 08b's QC: confirm none of these end up emitted from `adminme/projections/`.)

**Fixture work:**
- `tests/fixtures/governance.yaml` — minimal action_gates + rate_limits + hard-refuse list per BUILD.md §AUTHORITY example. Tagged `# fixture:tenant_data:ok` if it references any names. (BUILD.md's example uses `<persona.handle>` placeholders so no tenant identity bleed.)
- `tests/fixtures/authority.yaml` — same.

**Invariants in scope:** `[§6.5-8]` guardedWrite three-layer order + hard_refuse non-overridability + review-gate semantics, `[§6.13-16]` observation enforcement at final outbound only, default-on, per-tenant scope, `observation.suppressed` payload completeness, `[§14.1-4]` proactive-behavior scheduling boundary (governance gates apply to standing-order-fired pipelines too).

**Out of scope (08b):**
- The Node console's parallel guardedWrite (14a).
- OpenClaw exec-approvals integration (15 — document the composition seam in comments only).
- The Plaid `observation_mode_active` payload field on `xlsx.regenerated` (already shipped in 07c-β; no work here).
- Authority gate's full action matrix (the YAML's actor-by-action grid is tenant-configurable; 08b ships the mechanism + a fixture, not the production gates).

**Tests (08b):**
- `tests/unit/test_governance.py` — every guardedWrite path: allow / review (returns 202 + emits `review_request`) / deny / hard_refuse (non-overridable; verify even with admin-equivalent session it still refuses); rate limit exhaustion (sliding window correctness); short-circuit-on-first-refusal ordering; `write.denied.layer_failed` correctness for each layer. ≤ ~340 lines.
- `tests/unit/test_observation.py` — `outbound()` suppresses when active (no `action_fn` call; `observation.suppressed` event has full payload); when inactive, `action_fn` runs and `external.sent` lands; `ObservationManager.enable/disable` round-trip via events; default-on for fresh instance. ≤ ~260 lines.
- Integration `tests/integration/test_security_end_to_end.py` — the realistic flow from prompt 08's spec: principal A view-as principal B → completes a task that B owns → `task.completed.actor_member_id = A`, `task.completed.owner_member_id = B`, the task's privileged content is redacted in A's read of B's projections. ≤ ~180 lines (single test, end-to-end). **This integration test consumes 08a's Session AND 08b's governance gate; it cannot ship until both 08a and 08b are merged. Stays in 08b.**

**Per-commit budget check (§2.9):**

| Commit | Production code | Test code | Files read |
|---|---|---|---|
| 1 | `governance.py` populated (~390 lines) + 5 new event-type schemas | `test_governance.py` (~340 lines, 1 file) | 5: `BUILD.md §AUTHORITY`, `SYSTEM_INVARIANTS.md §6.5-8`, `CONSOLE_PATTERNS.md §3`, `DIAGRAMS.md §3`, `qc_rubric.md` |
| 2 | `observation.py` populated (~270 lines); `governance.yaml` + `authority.yaml` fixtures | `test_observation.py` (~260 lines, 1 file) | 4: `BUILD.md §OBSERVATION`, `SYSTEM_INVARIANTS.md §6.13-16`, `CONSOLE_PATTERNS.md §11`, `DIAGRAMS.md §9` |
| 3 | Integration wiring: register the 5 new event types in `adminme/events/schemas/`; document the OpenClaw exec-approvals composition seam in `governance.py` module docstring; identify any reverse-daemon emit site in 07c-β that needs to route through `outbound()` and **document UT-7's resolution path** (the actual rewrite stays in 08b's Commit 3 if it fits, otherwise becomes a 08.5 sidecar memo) | `test_security_end_to_end.py` (~180 lines, 1 file) | 4: `adminme/events/schemas/system.py`, `adminme/daemons/xlsx_sync/reverse.py`, `prompts/07c-beta-daemon.md` evidence, BUILD_LOG entry for 07c-β |
| 4 | BUILD_LOG; `verify_invariants.sh`; mypy preflight | — | 1: `qc_rubric.md` template |

**Sizing verdict for 08b:** within budgets but Commit 3 is the risk concentration. If UT-7's reverse-daemon rewrite turns out to require >100 lines of changes to `adminme/daemons/xlsx_sync/reverse.py`, that Commit 3 becomes a candidate for moving the rewrite to a separate sidecar (`prompts/08.5-reverse-daemon-outbound-rewrite.md`, est. 15-25 minutes). Partner refactoring 08b will probe `reverse.py` first; if the change is mechanical (wrap each emit site with `outbound()` and route through Session), it stays in 08b Commit 3; if it touches more than one structural seam, sidecar.

Total prompt target for 08b: ~290 lines / ~24 KB. Inline code: ~30-35 lines. All within budgets.

**Carry-forwards from 08b → prompt 09a:**
- `outbound()` API surface frozen (09a's skill-call wrapper does NOT route through `outbound()` because skill calls go to OpenClaw `:18789` loopback, not external; clarify in 09a's Read first).
- `GuardedWrite.check()` API surface frozen (13a/b consume it on every mutation route).

**Closes UT-7:** YES (assuming the reverse-daemon rewrite fits 08b Commit 3; otherwise 08.5 sidecar closes it).

---

## Dependency impact downstream

| Downstream prompt | Impact |
|---|---|
| 07.5 audit memo (already shipped) | None — audit didn't touch 08 territory. |
| 08a → 08b | Sequential. 08b consumes `Session` + `child_forbidden_tags` from 08a. No parallelization. |
| 09a (skill runner) | Mild: skill calls take a `Session` per `[§7.6]`. 09a's `Read first` should add `adminme/lib/session.py` to its read list. |
| 10a (pipeline runner) | Mild: pipeline handlers receive a `Session` (built by `build_internal_session` for proactive paths). |
| 13a/b (FastAPI services) | High: every mutation route calls `await guarded_write.check(session, action, payload)` first. 13a's `Read first` must add `adminme/lib/governance.py` and `adminme/lib/observation.py`. |
| 14a (Node console) | High: builds the Node-side parallel of `Session` + `guardedWrite`. 14a consumes 08a/08b as the Python-side reference. |
| 15 (OpenClaw integration) | Mild: documents that AdministrateMe's `guardedWrite` and OpenClaw's `exec-approvals` are independent gates (`[§6.7]` already states this). |

No prompt strictly requires a *renaming* in its existing draft text — 08a/08b together produce the same Session API the prompt-08 draft promised. The only `Read first` updates needed are in 09a, 10a, 13a, and 14a (additional pointer to `adminme/lib/session.py` etc.), and those updates happen at each prompt's refactor session, not in this split memo.

## What changes in `prompts/PROMPT_SEQUENCE.md`

In the sequence table:

- Replace the row `| 08 | 08-session-scope-governance.md | … | 4-5 hrs | … |` with two rows:
  - `| 08a | 08a-session-scope-read.md | Session dataclass + scope/privacy filter + wrap every projection query | 3-4 hrs | Reads correctly Session-scoped; ScopeViolation canary fires; privacy filter redacts privileged for non-owner reads |`
  - `| 08b | 08b-governance-observation-write.md | guardedWrite three-layer + observation outbound wrapper + 5 new event types | 3-4 hrs | Writes route through gate; observation suppresses external; UT-7 closes |`

In the dependency graph, replace the `08` node with `08a ──► 08b`. The `07.5 ──► 08` edge becomes `07.5 ──► 08a`; the `08 ──► 09a` edge becomes `08b ──► 09a`.

In the "Hard sequential dependencies" prose: 08a before 08b; 08b before 09a.

## What changes in `D-prompt-tier-and-pattern-index.md`

Replace the existing `08` row with:

- `08` row: pre-split disposition flips to **"Was split on arrival (08 → 08a/08b, 2026-04-25)"** and the row's content is preserved as historical record with sub-prompts listed beneath.
- `08a` new row: B / Introduction / Single prompt expected / 3-4 hrs / depth-read pointer = `BUILD.md §L3-CONTINUED`, `SYSTEM_INVARIANTS.md §6.1-4 + §6.9-12 + §6.17-18`, `CONSOLE_PATTERNS.md §2 + §6 + §7`, `DIAGRAMS.md §4 + §5`, all 10 projection `queries.py` files.
- `08b` new row: B / Introduction / Single prompt expected / 3-4 hrs / depth-read pointer = `BUILD.md §AUTHORITY,OBSERVATION,GOVERNANCE`, `SYSTEM_INVARIANTS.md §6.5-8 + §6.13-16 + §14`, `CONSOLE_PATTERNS.md §3 + §4 + §11`, `DIAGRAMS.md §3 + §9`, `adminme/daemons/xlsx_sync/reverse.py` (UT-7 probe).

In the "Pre-split candidates (NEW in v3)" list: remove `08` from the active list; move it under "Was split on arrival" in the index header narrative.

In the "Open UTs tracked in `partner_handoff.md`" list: UT-3 status updates to **"RESOLVED via 08 → 08a/08b split, 2026-04-25 (memo + sequence update PR)."**

## PR plan

This memo's only deliverable is **one single-purpose PR** updating the two documents above. No code, no prompt-file changes (08a and 08b draft prompts will be created in their respective subsequent refactor sessions). After James merges this PR:

- **Next session (Partner):** refactor 08a as Type 3 (refactor-only with Job 3). Produces `prompts/08a-session-scope-read.md` + prep PR.
- **Session after that:** James runs Claude Code on 08a → merges.
- **Session after that (Partner):** Type 1 — QC the 08a merge + refactor 08b.
- **Session after that:** James runs Claude Code on 08b → merges.
- **Session after that (Partner):** Type 2 — QC the 08b merge + close UT-7 + close UT-3.

Five Partner sessions to land 08 work, vs. the three (refactor / Claude Code / QC) that an 08 in one piece would nominally need — but this is the cost of avoiding the timeout that the 08 draft as written would predictably produce.

## End of memo
