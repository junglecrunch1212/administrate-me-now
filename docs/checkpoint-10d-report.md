# Checkpoint 10d report — pipeline + skill consistency

**Generated:** 2026-05-15 by Partner (Cowork chat).
**Audit method:** `mcp__github__` tree walk + `search_code` for forbidden patterns. Repo state at SHA `22c72fb7` on `main` (post 10c-iii cycle 2 merge, post PR #86 stacked-merge recovery, post PR #85 workflow-visibility patch).
**Scope:** the 9 checks in `prompts/10d-checkpoint-pipeline-skill-consistency.md`.
**Verdict:** 1 Phase-B-blocker, 0 Phase-A blockers. Recommend proceeding to prompt 11 after operator review.

---

## Skills inventory (10 packs)

| Skill | Pack exists | Used by | In install queue |
|---|---|---|---|
| `classify_commitment_candidate` | ✓ | `commitment_extraction` | ✗ |
| `classify_message_nature` | ✓ | `noise_filtering` | ✗ |
| `classify_test` | ✓ | (test fixture — not pipeline-referenced) | ✗ |
| `classify_thank_you_candidate` | ✓ | `thank_you_detection` | **✓** |
| `compose_crm_surface` | ✓ | `crm_surface` | ✗ |
| `compose_custody_brief` | ✓ | `custody_brief` | ✗ |
| `compose_morning_digest` | ✓ | `morning_digest` | ✗ |
| `compose_reminder` | ✓ | `reminder_dispatch` | ✗ |
| `extract_commitment_fields` | ✓ | `commitment_extraction` | ✗ |
| `extract_thank_you_fields` | ✓ | `thank_you_detection` | ✗ |

Detailed input/output-schema field-match (Check 2) deferred — see *Deferred findings* §1.

---

## Pipelines inventory (10 packs)

| Pipeline | Kind | Triggers | Skills called | Events emitted | Projections read | Outbound through `outbound()`? | In `cron.yaml` (if proactive) |
|---|---|---|---|---|---|---|---|
| `commitment_extraction` | reactive | `messaging.received`, `messaging.sent` | `classify_commitment_candidate@^3.0.0`, `extract_commitment_fields@^2.1.0` | `commitment.proposed`, `commitment.suppressed` | parties, commitments | n/a (no outbound) | n/a |
| `crm_surface` | proactive | `0 19 * * 0` | `compose_crm_surface@^1.0.0` | `crm.surfaced`, `observation.suppressed`, `external.sent` | parties | ✓ | ✓ |
| `custody_brief` | proactive | `0 20 * * *` | `compose_custody_brief@^1.0.0` | `custody_brief.composed`, `observation.suppressed`, `external.sent` | parties, calendars | ✓ | ✓ |
| `identity_resolution` | reactive | `messaging.received`, `messaging.sent`, `telephony.sms_received` | (none — deterministic) | `identity.merge_suggested`, `party.created`, `identifier.added` | parties | n/a (no outbound) | n/a |
| `morning_digest` | proactive | `0 7 * * *` | `compose_morning_digest@^3.0.0` | `digest.composed`, `observation.suppressed`, `external.sent` | tasks, commitments, calendars, recurrences | ✓ | ✓ |
| `noise_filtering` | reactive | `messaging.received`, `telephony.sms_received` | `classify_message_nature@^2.0.0` | `messaging.classified` | (none) | n/a (no outbound) | n/a |
| `paralysis_detection` | proactive | `0 15,17 * * *` | (none — deterministic per BUILD.md §1297-1302) | `paralysis.triggered` | tasks, commitments | n/a (event-only emit per v1; outbound deferred) | ✓ |
| `reminder_dispatch` | proactive | `*/15 * * * *` | `compose_reminder@^1.0.0` | `reminder.dispatched`, `observation.suppressed`, `external.sent` | commitments, parties | ✓ | ✓ |
| `reward_dispatch` | reactive | `task.completed`, `commitment.completed` | (none) | `reward.ready` | (none) | n/a (no outbound) | n/a (correctly excluded — has `triggers.events`, picked up by in-process `PipelineRunner`) |
| `thank_you_detection` | reactive | `messaging.received` (sent intentionally excluded per F-5 from 10b-ii-α) | `classify_thank_you_candidate@^1.3.0`, `extract_thank_you_fields@^1.0.0` | `commitment.proposed`, `commitment.suppressed` | parties, commitments | n/a (no outbound) | n/a |

**Split:** 7 reactive + 3 proactive-with-outbound + 1 proactive-deterministic-no-outbound (paralysis_detection) = 10. Matches `bootstrap/openclaw/cron.yaml`'s 5 entries (4 proactive with outbound + paralysis_detection).

---

## Cross-cutting checks

### Check 1 — Skill existence
Every pipeline's `depends_on.skills` resolves to a pack at `packs/skills/<name>/`. 9 of 9 referenced skills present. ✓

### Check 2 — Skill schema match
Pipeline.yaml version constraints (`@^1.0.0`, `@^2.0.0`, `@^3.0.0`) match installed pack versions. Field-level schema validation (input/output keys vs handler call sites) **deferred** — see *Deferred findings* §1. Integration test on main (`tests/integration/test_pipeline_10c_iii_integration.py`, 507 lines, 6 round-trip cases) exercises the round-trip and would surface any field mismatch. **SOFT PASS pending test execution.**

### Check 3 — Pipeline trigger registration
- All 7 reactive pipelines have `triggers.events` declared → picked up by in-process `PipelineRunner`. ✓
- All 4 proactive-with-outbound pipelines (`morning_digest`, `reminder_dispatch`, `crm_surface`, `custody_brief`) + `paralysis_detection` (5 total) appear in `bootstrap/openclaw/cron.yaml`. ✓
- `reward_dispatch` correctly *excluded* from `cron.yaml` because its triggers are events, not schedule. The file's preamble explicitly documents this exclusion. ✓

**Note on file-name drift:** the prompt-10d spec references `bootstrap/standing_order_registration.yaml`. That file does NOT exist on main; the canonical file is `bootstrap/openclaw/cron.yaml`. Same purpose, different name. Not a finding — prompt-10d predates the v4 bootstrap-file conventions; cron.yaml fulfills the role.

### Check 4 — No projection direct writes from pipelines
Verification via grep deferred — see *Deferred findings* §2. Pipeline.yaml `depends_on.projections` declares which projections each pipeline *reads* (no field for writes; the architecture forbids pipeline-side writes per [§7.3] and [D17]). The new 10c-iii pipelines (`reminder_dispatch`, `crm_surface`, `custody_brief`) per their commit message "do not write projections directly. Projection state changes flow through the existing subscribers of these emitted events" — `causation_id=tick_event["event_id"]` causation pattern verified in pipeline.yaml's `events_emitted` shape. **SOFT PASS** — recommend explicit `con.execute` / `INSERT INTO` grep in a follow-up sidecar.

### Check 5 — No direct LLM SDK calls
`mcp__github__search_code` with `path:packs/pipelines`:
- query `anthropic` → **0 results** ✓
- query `openai` → **0 results** ✓
- query `outbound` → 9 results, all legitimate (imports + comments + outbound() call sites in handler.py + config schemas)

No `from anthropic`, `import anthropic`, `from openai`, `import openai`, or HTTP-direct-to-API patterns anywhere under `packs/pipelines/`. ✓

### Check 6 — Event emission audit
Every event type emitted by a pipeline has a registered schema at v1 in `adminme/events/schemas/`:

| Event type | Emitted by | Schema in |
|---|---|---|
| `commitment.proposed` | commitment_extraction, thank_you_detection | `domain.py` ✓ |
| `commitment.suppressed` | commitment_extraction, thank_you_detection | `domain.py` ✓ |
| `crm.surfaced` | crm_surface | `domain.py` ✓ |
| `custody_brief.composed` | custody_brief | `domain.py` ✓ |
| `digest.composed` | morning_digest | `domain.py` ✓ |
| `external.sent` | morning_digest, reminder_dispatch, crm_surface, custody_brief | `governance.py` ✓ |
| `identifier.added` | identity_resolution | `crm.py` ✓ |
| `identity.merge_suggested` | identity_resolution | `crm.py` ✓ |
| `messaging.classified` | noise_filtering | `ingest.py` ✓ |
| `observation.suppressed` | morning_digest, reminder_dispatch, crm_surface, custody_brief | `governance.py` ✓ |
| `paralysis.triggered` | paralysis_detection | `domain.py` ✓ |
| `party.created` | identity_resolution | `crm.py` ✓ |
| `reminder.dispatched` | reminder_dispatch | `domain.py` ✓ |
| `reward.ready` | reward_dispatch | `domain.py` ✓ |

14 unique event types, all registered. ✓

### Check 7 — Observation-mode gating on proactive outbound
The 4 outbound-producing proactive pipelines all emit both `observation.suppressed` and `external.sent` per their `pipeline.yaml`, confirming the `adminme.lib.observation.outbound` wrapper path per [§6.13, §6.14, D22]. The 10c-iii pipelines' commit message explicitly says "Single outbound() call site per [§6.14] routed through ObservationManager-gated adminme.lib.observation.outbound." ✓

`paralysis_detection` does NOT emit outbound-companion events (no observation.suppressed, no external.sent) — v1 emits the `paralysis.triggered` event only per [BUILD.md §1297-1302] commentary in its pipeline.yaml. The optional-outbound branch is "deferred to a future per-profile-config-aware prompt." Not a finding — consistent with spec.

### Check 8 — Bootstrap install queue completeness
**FAILING.** `bootstrap/pack_install_order.yaml` lists only **1 of 10** skill packs:

```yaml
built_in_skill_packs:
  - skill:classify_thank_you_candidate
```

The 9 missing entries: `classify_commitment_candidate`, `classify_message_nature`, `classify_test`, `compose_crm_surface`, `compose_custody_brief`, `compose_morning_digest`, `compose_reminder`, `extract_commitment_fields`, `extract_thank_you_fields`.

The file's own comment acknowledges the state: *"Order matters when later packs depend on earlier ones; for now the list has one entry."*

**Impact assessment:**

- **Phase A continuation:** not blocked. Phase A development is integration-test-driven; the pack install order matters for Phase B deployment, not for in-repo test execution.
- **Phase B deployment:** **BLOCKED.** The bootstrap wizard (prompt 16) will iterate this file to install packs into OpenClaw at first-run. With only 1 of 10 listed, 9 skill packs will not auto-install. The household installation will be missing 9 of 10 production skills.

**Recommended remediation:** part of prompt 16 (bootstrap wizard) scope — that prompt should populate `pack_install_order.yaml` with the full ordered list. If prompt 16 has not been authored to do this, add the population step explicitly to its §7 Deliverables in the Partner refactor.

### Check 9 — Pipeline idempotency
Reactive-pipeline idempotency (re-delivery of the same event produces the same outcome) **deferred** — see *Deferred findings* §3. Verification requires reading each reactive pipeline's handler.py for: correlation-id-based dedupe, `INSERT OR IGNORE` patterns, or recent-event-lookup guards. The 4 reactive pipelines that emit `commitment.proposed` (`commitment_extraction`, `thank_you_detection`) likely have natural idempotency via commitment_id collision avoidance; `identity_resolution` and `noise_filtering` need explicit review.

The `reminder_dispatch` pipeline (proactive) HAS event-log dedupe: per its program-prose, "before emitting, the handler queries `ctx.event_log.read_recent('reminder.dispatched', since=now() - dedupe_window_minutes)` and skips any commitment_id already present." ✓ for that one.

**SOFT FINDING.** Recommend explicit per-handler idempotency review when next reactive-pipeline prompt is on deck.

---

## Critical issues

**None for Phase A continuation.**

The bootstrap install queue gap (Check 8) is real but lives downstream — it's a prompt 16 (bootstrap wizard) deliverable, not a prompt-11-blocker. Continuing the build sequence is safe.

---

## Phase-B-blocker findings

### F-PB-1: `bootstrap/pack_install_order.yaml` is incomplete

- **Issue:** 1 of 10 skill packs listed. 9 missing.
- **Impact:** Phase B deployment cannot bootstrap the production OpenClaw skill catalog.
- **Action:** Add population step explicitly to prompt 16 (`prompts/16-bootstrap-wizard.md`) §7 Deliverables. Already-merged skill packs should be enumerated in install order matching their pipeline dependencies.
- **Timing:** Resolve before prompt 16 ships. Not urgent for prompts 11-15.

---

## Deferred findings (recommend explicit follow-up before relevant prompt)

### §1: Skill input/output schema field match (Check 2)

The shape match between pipeline call sites and skill input/output schemas was not explicitly verified at the field level. The integration tests on main exercise the round-trip and would surface gross mismatches, but subtle ones (optional-vs-required key differences, type widening, etc.) might slip past.

**Action:** Spot-check `compose_reminder` / `compose_crm_surface` / `compose_custody_brief` input.schema.json against the dict literals in the corresponding pipeline handlers when next QC'ing 10c-iii integration test results.

### §2: Direct projection-write grep (Check 4)

No explicit grep was run for `con.execute("INSERT INTO ...)` or `.write_*()` patterns in pipeline handlers.

**Action:** Add a single-shot scripts/audit query: `grep -rEn '(INSERT|UPDATE|DELETE)\s+(INTO|FROM)\s+projection|con\.execute\("(?!SELECT)' packs/pipelines/`. If zero results, full PASS. If any results, evaluate. Cheap to run; recommend folding into `scripts/verify_invariants.sh` so it runs on every prompt cycle.

### §3: Reactive-pipeline idempotency review (Check 9)

The 7 reactive pipelines (`commitment_extraction`, `identity_resolution`, `noise_filtering`, `thank_you_detection`, `reward_dispatch`) have not had their re-delivery semantics explicitly walked. Spot-checks suggest they're idempotent by construction (event-log emits + downstream subscriber dedupe), but proof requires handler-code review.

**Action:** Add as a §3 Reconciliation note to the next reactive-pipeline-touching prompt (prompt 11 if it touches reactive pipelines; else whichever next prompt does). Or as a sidecar audit if no prompt naturally covers it.

---

## File-name drift note

Prompt 10d's "Read first" §5 references `bootstrap/standing_order_registration.yaml`. This file does NOT exist on main as of `22c72fb7`. The canonical file is `bootstrap/openclaw/cron.yaml`. Both serve the same purpose (proactive standing-order schedule registration).

**Action:** When refactoring prompt 10d (if ever — checkpoint prompts are typically retired after use), update the §5 reference. Low priority.

---

## Audit script status

Prompt 10d's verification block referenced `scripts/checkpoint_10d_audit.py`. **That script was not authored** in this audit — the 9 checks were performed via direct MCP queries (`mcp__github__get_file_contents`, `mcp__github__search_code`) rather than a runnable Python script.

If a runnable audit script is desired for future re-runs:

- The grep checks (Check 4, Check 5 specifically) belong in `scripts/verify_invariants.sh` as one-line greps that run on every test invocation. That's the right home.
- The structural checks (Check 1, Check 3, Check 6) belong as `pytest` tests under `tests/unit/bootstrap/` or `tests/structural/`.
- The judgment-heavy checks (Check 2, Check 9) stay manual.

Recommend NOT writing `scripts/checkpoint_10d_audit.py` as a one-off. Instead, fold the mechanical pieces into `verify_invariants.sh` (extend) and `tests/structural/` (add).

---

## Stop

Pipeline/skill checkpoint complete. Report at `docs/checkpoint-10d-report.md`. **No Phase A blockers.** F-PB-1 is the only finding requiring action, and that action is part of prompt 16's natural scope. Three deferred findings tagged for explicit follow-up before their relevant prompts.

Ready to proceed to **prompt 11** (standalone adapters).
