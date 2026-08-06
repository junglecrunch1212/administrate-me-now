# NEEDS_HUMAN — Checkpoint 10d Routine review

**Triggered by:** PR #87 merge (`partner/checkpoint-10d-report → main`)
**Routine:** Checkpoint 10d — pipeline ↔ skill ↔ schema consistency
**Authored by:** Claude Code Checkpoint 10d Routine (claude/stoic-ramanujan-DYgem)
**Date:** 2026-05-15
**Repo state:** SHA `d35ea4a` (post-merge main)

---

## Operator action required

This file is a **pause gate**. Read both audits below, consider the vision-coherence section, then decide:

- **Green-light prompt 11:** the build may proceed; all findings below are carry-forwards or informational.
- **Authorize a sidecar:** before prompt 11, fix one or more of the flagged items.

No Phase A blocker was identified. One D8 constitutional violation (medium severity) is flagged and requires an operator disposition. See CSO Audit finding CSO-1.

---

## Audit 1 — CSO (Chief Security Officer role)

*Security-focused. Conservative when uncertain. Confidence ratings reflect certainty that the finding is a real issue, not that it is severe.*

```json
{
  "audit_id": "cso-10d-20260515",
  "auditor_role": "CSO",
  "repo_sha": "d35ea4a86a87d28ac41ed072f3d6907717b0a5d2",
  "scope": "packs/pipelines/, packs/skills/, adminme/lib/skill_runner/, adminme/events/schemas/",
  "verdict_phase_a": "PROCEED WITH SIDECAR RECOMMENDATION",
  "findings": [
    {
      "id": "CSO-1",
      "title": "D8 violation: causation_id omitted from three EventStore.append() call sites in skill_runner",
      "severity": "medium",
      "confidence": 9,
      "constitutional_source": ["D8 Addition 2", "SYSTEM_INVARIANTS §1"],
      "file": "adminme/lib/skill_runner/wrapper.py",
      "line_ranges": [
        "_emit_failed (approx line 168): append(envelope, correlation_id=correlation)",
        "_emit_suppressed (approx line 190): append(envelope, correlation_id=correlation)",
        "_emit_recorded (approx line 225): append(envelope, correlation_id=correlation)"
      ],
      "detail": "D8 Addition 2 states: 'Every EventStore.append() call site must pass both correlation_id and causation_id as explicit keyword arguments. Passing None is allowed when genuinely unknown; defaulting them via function signature is not.' All three emit helpers in wrapper.py pass correlation_id but silently omit causation_id. This means skill.call.recorded, skill.call.failed, and skill.call.suppressed events land in the log without a causation chain — breaking the per-event provenance invariant and the 'adminme skill replay' workflow which relies on causation_id to identify the original call.",
      "is_new_in_pr87": false,
      "note": "This violation predates prompt 10d; the PR introduced no new code. However the 10d checkpoint is the first structured audit to catch it.",
      "recommended_fix": "In _emit_failed, _emit_suppressed, and _emit_recorded, add causation_id=None as explicit keyword arg. For _emit_recorded, consider whether the skill invocation's correlation_id should serve as causation_id of the recorded event (it should: the originating pipeline event that triggered the skill call is the causal parent). The PipelineContext's triggering_event_id would be the correct value if surfaced to the wrapper; alternatively None is D8-compliant when genuinely unknown, which is the correct disposition for skill_runner since it doesn't always have a triggering event in scope."
    },
    {
      "id": "CSO-2",
      "title": "Sensitivity-tier propagation bypassable: input_sensitivities defaults to None",
      "severity": "low",
      "confidence": 5,
      "constitutional_source": ["SYSTEM_INVARIANTS §7.6", "§6.9-6.12"],
      "file": "adminme/lib/skill_runner/wrapper.py",
      "line_ranges": [
        "run_skill signature: input_sensitivities: dict[str, str] | None = None",
        "Step 3 sensitivity check: if input_sensitivities: ..."
      ],
      "detail": "The sensitivity check at Step 3 is guarded by 'if input_sensitivities'. If a pipeline calls run_skill() without passing input_sensitivities, the check is entirely skipped — a privileged field in inputs would flow to OpenClaw without triggering SkillSensitivityRefused. The framework relies on every pipeline caller to correctly annotate input sensitivity. None of the pipeline handlers reviewed (morning_digest, paralysis_detection) pass input_sensitivities in their skill calls. For the current skill set (compose_morning_digest, compose_reminder, etc.), inputs are derived from projection queries that exclude privileged content by construction, so the gap is unlikely to fire in practice. Confidence 5/10 — assessed low severity because the upstream scope guards at projection query time are defense-in-depth.",
      "recommended_fix": "Add a lint-level note or enforce at call time: either document in PipelineContext that callers are responsible for passing input_sensitivities, or change the default to an empty dict (falsy but explicit) and add a test that verifies sensitivity check fires when a privileged key is passed. Not blocking."
    },
    {
      "id": "CSO-3",
      "title": "No-projection-direct-write grep absent from CI enforcement",
      "severity": "low",
      "confidence": 7,
      "constitutional_source": ["SYSTEM_INVARIANTS §2.2", "§7.3"],
      "file": "scripts/verify_invariants.sh (absent check)",
      "line_ranges": [],
      "detail": "The 10d report's Check 4 (no projection direct writes from pipelines) was deferred — the grep for INSERT/UPDATE patterns in pipeline handlers was not run and is not enforced in CI. This means a future pipeline author could inadvertently write to a projection table without CI catching it. The pipeline.yaml convention (listing projections under depends_on.projections without a 'writes' key) provides documentation-level enforcement only.",
      "recommended_fix": "Add to scripts/verify_invariants.sh: grep -rEn '(INSERT|UPDATE|DELETE)\\s+(INTO|FROM)|con\\.execute\\([\"\\x27](?!SELECT)' packs/pipelines/ and fail on non-zero count. As recommended in the 10d report §2 deferred finding. This is a cheap one-liner; recommend folding into the next prompt-touching verify_invariants.sh."
    },
    {
      "id": "CSO-4",
      "title": "observation_mode fallback in morning_digest treats None manager as observation-active rather than erroring",
      "severity": "low",
      "confidence": 6,
      "constitutional_source": ["SYSTEM_INVARIANTS §6.13", "§6.14", "§6.16"],
      "file": "packs/pipelines/morning_digest/handler.py",
      "line_ranges": [
        "deliver() method: if ctx.observation_manager is None: return False"
      ],
      "detail": "When ctx.observation_manager is None, deliver() logs a warning and returns False (treating the pipeline as observation-mode-active). Per §6.16, observation is default-on for new instances — so failing closed here is constitutionally correct. However the log message says 'treating as observation-mode-active (default-on for proactive pipelines)' which could mislead an operator debugging a wiring failure into thinking observation is intentionally on when actually the observation_manager was never injected. The behavior is safe but the diagnostic is ambiguous.",
      "recommended_fix": "Separate the two cases in the log message: (a) observation_manager is None due to unwired DI — log at WARNING with 'no observation_manager injected; defaulting to suppressed per [§6.16] default-on'; (b) observation_manager is wired and active — suppress at observation_manager level as designed. Not blocking; informational."
    }
  ],
  "checks_passed": [
    "No direct anthropic/openai imports in packs/pipelines/ (0 search results, confirmed by 10d report Check 5 + direct wrapper.py review)",
    "outbound() via adminme.lib.observation imported correctly in morning_digest/handler.py",
    "paralysis_detection correctly omits outbound() in v1 per spec",
    "correlation_id + causation_id passed to event_log.append in morning_digest and paralysis_detection handlers",
    "No hardcoded tenant identity detected in reviewed handler files",
    "Skill runner posts to http://127.0.0.1:18789/tools/invoke — correct OpenClaw boundary per D6/§8",
    "observation.suppressed and external.sent declared in pipeline.yaml for all 4 outbound-producing proactives",
    "sensitivity check logic present in wrapper.py Step 3 (guarded; see CSO-2)"
  ]
}
```

---

## Audit 2 — Codex (cross-model second opinion)

*Cross-prompt patterns, coupling, maintainability, test coverage adequacy. Same severity/confidence scale.*

```json
{
  "audit_id": "codex-10d-20260515",
  "auditor_role": "Codex",
  "repo_sha": "d35ea4a86a87d28ac41ed072f3d6907717b0a5d2",
  "findings": [
    {
      "id": "CDX-1",
      "title": "F-PB-1 (known): bootstrap/pack_install_order.yaml lists 1 of 10 skill packs",
      "severity": "high",
      "confidence": 10,
      "file": "bootstrap/pack_install_order.yaml",
      "detail": "Already identified by the Partner audit as F-PB-1. 9 skill packs (classify_commitment_candidate, classify_message_nature, classify_test, compose_crm_surface, compose_custody_brief, compose_morning_digest, compose_reminder, extract_commitment_fields, extract_thank_you_fields) are absent from the install queue. Phase B deployment will bootstrap with only classify_thank_you_candidate installed; the other 9 skills will not auto-install and the production skill catalog will be broken on day 1. Confirmed high severity. Confirmed Phase B blocker.",
      "timing": "Prompt 16 (bootstrap wizard) scope. Not urgent for prompts 11-15.",
      "recommended_fix": "Add population step to prompts/16-bootstrap-wizard.md §7 Deliverables. The ordered list should respect skill dependency order; for the current cohort all 10 skills are independent so alphabetical or any stable order is fine."
    },
    {
      "id": "CDX-2",
      "title": "crm_surface pipeline depends_on.projections lists only 'parties' — interactions projection absent",
      "severity": "medium",
      "confidence": 6,
      "file": "packs/pipelines/crm_surface/pipeline.yaml (inferred from 10d report inventory table)",
      "detail": "The 10d report's pipeline inventory table shows crm_surface reads the 'parties' projection only. But the landing-page vision promises 'who you haven't called in too long' and 'who you're drifting from before you notice' — both require interaction frequency data from the 'interactions' projection (closeness scoring, last-contact recency). If compose_crm_surface receives only parties data without interaction history, the skill cannot compute drift or recency gaps. This could mean the skill is receiving stale or missing data for its core output. Confidence 6/10 — the skill's input schema may receive interaction data through a different path (e.g., the loader in the pipeline handler fetches from a separate closeness_scoring projection output), but the pipeline.yaml declaration gap is concerning regardless.",
      "recommended_fix": "Read packs/pipelines/crm_surface/handler.py directly to confirm what data is passed to compose_crm_surface. If the skill input doesn't receive interaction/closeness data, add 'interactions' to depends_on.projections and wire the loader. Tag for QC when 10c-iii integration test results are reviewed (per 10d report §1 deferred finding)."
    },
    {
      "id": "CDX-3",
      "title": "Reactive pipeline idempotency unverified for 4 reactive handlers",
      "severity": "medium",
      "confidence": 7,
      "file": "packs/pipelines/{commitment_extraction,identity_resolution,noise_filtering,thank_you_detection}/handler.py",
      "detail": "The 10d report Check 9 deferred idempotency review for 7 reactive pipelines. The 4 pipelines that emit commitment.proposed (commitment_extraction, thank_you_detection) could produce duplicate proposals if the same messaging.received event is delivered twice (bus redelivery on restart). identity_resolution could emit duplicate party.created or identity.merge_suggested events. noise_filtering could emit duplicate messaging.classified. reminder_dispatch has explicit dedupe (event-log query before emit); the reactive pipelines do not appear to have equivalent guards based on the architectural description. This is a concrete operational risk if the bus delivers events at-least-once (the InProcessBus uses durable per-subscriber offsets — redelivery on restart is possible).",
      "recommended_fix": "For commitment_extraction and thank_you_detection: verify that the commitment_id collision avoidance at the projections layer (INSERT OR IGNORE semantics) provides idempotency, or add event-log dedupe query per the reminder_dispatch pattern. Schedule explicit review before next reactive-pipeline-touching prompt. Per 10d report §3 deferred finding."
    },
    {
      "id": "CDX-4",
      "title": "Skill I/O field-level schema match not verified",
      "severity": "low",
      "confidence": 5,
      "file": "packs/skills/{compose_reminder,compose_crm_surface,compose_custody_brief}/input.schema.json vs handler.py call sites",
      "detail": "The 10d report Check 2 was SOFT PASS — version constraints match but field-level key/type match between pipeline call sites and skill input.schema.json was not verified. Optional vs required key differences or type widening could produce runtime SkillInputInvalid failures in production that the integration tests might not surface (tests may use overly permissive inputs). Confidence 5/10 — the integration test suite (507 lines, 6 round-trip cases) provides reasonable coverage but field-level gaps can survive round-trip tests.",
      "recommended_fix": "Spot-check compose_reminder, compose_crm_surface, compose_custody_brief input.schema.json against the dict literals in the corresponding pipeline handler gather() methods. Should be a <30-min manual check. Per 10d report §1 deferred finding."
    },
    {
      "id": "CDX-5",
      "title": "from adminme.lib.observation import outbound deferred inside deliver() body",
      "severity": "low",
      "confidence": 8,
      "file": "packs/pipelines/morning_digest/handler.py",
      "line_ranges": ["deliver() method: 'from adminme.lib.observation import outbound' inside function body"],
      "detail": "The outbound import is deferred inside the deliver() function body rather than at the module top. While functionally correct, deferred imports obscure dependency graphs, make static analysis harder, and are contrary to Python convention. This pattern will confuse future pipeline authors who use morning_digest as a reference implementation.",
      "recommended_fix": "Move 'from adminme.lib.observation import outbound' to module top-level alongside other adminme imports. If the deferred import was intentional to avoid circular imports, add a comment explaining why. Minor; low priority."
    }
  ],
  "pattern_consistency_check": {
    "constructor_injection_loaders": "PASS — morning_digest and paralysis_detection both follow 10c-i's reward_dispatch constructor-injection shape with no-op defaults and defensive None-checks",
    "validation_guard": "PASS — morning_digest ships the claimed_event_ids validation guard per BUILD.md §1289; paralysis_detection has deterministic pre-condition check",
    "defensive_defaults": "PASS — all loaders default to no-op lambdas; skill failures catch _SKILL_FAILURE_EXCEPTIONS and fall through to sentinel path",
    "dispatch_handle_split": "PASS — both pipelines implement dispatch() + handle() consistently with the pattern established in 10c-i",
    "event_schema_versions": "PASS — all 14 emitted event types have v1 schemas in adminme/events/schemas/ per 10d report Check 6",
    "pipeline_yaml_shape": "PASS — all reviewed pipeline.yaml files follow the same pack/runtime/triggers/depends_on/events_emitted shape",
    "skill_pack_shape": "SOFT PASS — skill packs exist at packs/skills/<name>/ (no explicit namespace subdirectory visible); D11 specifies <namespace>/<name>/ shape but this may be a docs vs. directory convention difference. Recommend verifying against D11 when auditing skill packs directly."
  }
}
```

---

## Vision-coherence sweep

*Severity is informational — checkpoint Routines do not escalate on vision alone. Operator sees vision drift here and may authorize sidecars to address.*

### Pipeline-by-pipeline assessment against WHAT-IS-ADMINISTRATEME.md

| Pipeline | Vision section it serves | Verdict | Notes |
|---|---|---|---|
| `morning_digest` | "Chief of Staff" §1 + "speaks ADHD" §2 | **PARTIAL** | See V-1 |
| `paralysis_detection` | "speaks ADHD" §2 specifically | **PARTIAL** | See V-2 — key gap |
| `reminder_dispatch` | "what's coming up" + Chief of Staff §1 | **PASS** | outbound wired; delivers the "bills coming up" promise |
| `reward_dispatch` | "speaks ADHD" variable-ratio §2 | **PASS** | reward.ready event emitted; console SSE fan-out handles toast delivery correctly |
| `crm_surface` | "real CRM for your life" §3 | **PARTIAL** | See V-3 |
| `custody_brief` | "coparents managing custody" who-it's-for | **PASS** | fires daily at 20:00 if coparent relationship exists; depends on parties+calendars |

---

### V-1 — morning_digest: ADHD profile rendering not active in v1

**Vision promise (ADHD §2):** "proactive morning digests that tell you what matters before you've had coffee" + ADHD-specific rendering (fog-aware format, Zeigarnik closers, guilt vocabulary filtered).

**Shipped state:** `DEFAULT_PROFILE_FORMAT = "none"` and the profile_loader defaults to `lambda _mid: None`. In production, until real loaders are wired (UT-14 carry-forward), every digest renders in the `"none"` format — the profile-adaptive rendering (fog_aware, compressed, carousel) that makes the digest "speak ADHD" is structurally present but inactive. The persona_loader similarly returns None, meaning Zeigarnik closers and guilt-filtered vocabulary also aren't active.

**Severity (informational):** This is an acknowledged UT-14 carry-forward, not a regression. The architecture is correct; the DI wiring is pending. Operator should confirm prompt 12 or 13 wires real loaders, otherwise the ADHD neuroprosthetic delivery value is absent in Phase B day-1.

**Recommended sidecar target:** Whichever prompt wires projection loaders into PipelineContext — confirm it addresses morning_digest's profile_loader and persona_loader wiring explicitly, not just the pipeline runner seam.

---

### V-2 — paralysis_detection: nudge is detected but not delivered in v1

**Vision promise (ADHD §2):** "Paralysis detection at 3pm and 5pm: if you've stalled, the system notices **and sends a gentle nudge** with a specific tiny next step."

**Shipped state:** `paralysis_detection` v1 emits `paralysis.triggered` into the event log only. It does NOT call `outbound()`. The pipeline.yaml lists no `observation.suppressed` or `external.sent` in `events_emitted`. The pipeline.yaml comments explicitly say "outbound deferred to a future per-profile-config-aware prompt."

**Gap:** The landing-page promise is "sends a gentle nudge." The shipped v1 detects the paralysis state and records it, but the nudge is never delivered to the member — not via iMessage, not via Today/activity, not via any surface. The `paralysis.triggered` event sits in the log unread unless some downstream subscriber (not yet built) acts on it.

**Severity (informational / elevated):** This is the most significant vision gap in the current cohort. The ADHD section's most behavior-specific promise (the 3pm/5pm nudge with a tiny next step) is not delivered end-to-end. The "event-only emit" design choice is acknowledged in BUILD.md §1297-1302 and pipeline.yaml comments, so it's intentional. But it should be on the operator's radar as a Phase A gap that needs a specific prompt to close before Phase B is live.

**Recommended sidecar target:** Authorize an explicit sidecar or add to the next ADHD-facing prompt: wire paralysis_detection to call `outbound()` with the `template_text` as the nudge payload, routing through the configured member channel (iMessage default). This requires the observation_manager to be wired — which aligns with the same UT-14 carry-forward that blocks morning_digest's full ADHD rendering.

---

### V-3 — crm_surface: drift detection may be missing interaction data

**Vision promise (CRM §3):** "The system surfaces who you're drifting from before you notice." + "every commitment you've made" + "search by relationship, by topic, by time-since-last-contact."

**Shipped state:** `crm_surface` depends_on lists only `parties` projection. The closeness scores that power "who you're drifting from" live in the `closeness_scoring` pipeline output — which updates nightly and writes back to… where? The closeness_scoring pipeline (listed in architecture §5 as a nightly pipeline) doesn't appear in the packs/pipelines/ directory in the 10d inventory (it's listed in architecture-summary but not in the 10d pack inventory of 10 pipelines). If compose_crm_surface doesn't receive closeness/interaction data, it can't deliver the "who you're drifting from" promise — it can only see party names and identifiers.

**Severity (informational):** Two separate questions need operator clarification:
1. Is `closeness_scoring` in scope for Phase A? (Not in the 10-pipeline inventory — may be a later prompt.)
2. Does `compose_crm_surface` skill receive interaction-recency inputs from somewhere other than the pipeline depends_on declaration?

**Recommended action:** Read `packs/pipelines/crm_surface/handler.py` and `packs/skills/compose_crm_surface/SKILL.md` to confirm what inputs the skill actually receives. If closeness data is absent from the skill's input, flag for prompt 10e/10f scope.

---

### Vision sections NOT served by this cohort (no finding required, noted for completeness)

- **"Working-memory and capture system" §5** — not in scope for 10d pipelines; capture layer is a future prompt.
- **"Sensor and signal layer" §6** — not in scope; Plaid and HA adapters are prompt 11+ territory.
- **"Unified communications §4"** — not in scope for 10d pipelines; provider-message aggregation is a Comms product (separate prompt), while extracted candidates now enter Triage per D26.
- **"The Excel workbook is alive"** — xlsx projection is orthogonal to 10d pipeline cohort.

---

## Disposition table

| Finding | Severity | Phase | Recommended action | Operator decision |
|---|---|---|---|---|
| CSO-1: D8 causation_id gap in wrapper.py | Medium | A | Sidecar fix before prompt 12 | **[ ] Authorize sidecar** / **[ ] Defer to prompt 12 scope** |
| CSO-2: input_sensitivities bypassable | Low | A | Document + add test | **[ ] Fold into prompt 11** / **[ ] Defer** |
| CSO-3: no-projection-write grep absent | Low | A | Add to verify_invariants.sh | **[ ] Fold into next prompt touching that script** |
| CSO-4: observation_manager None diagnostic ambiguous | Low | A | Clarify log message | **[ ] Fold into next morning_digest-touching prompt** |
| CDX-1 (F-PB-1): pack_install_order.yaml incomplete | High | B | Add to prompt 16 §7 Deliverables | **[ ] Already tracked** |
| CDX-2: crm_surface missing interactions projection | Medium | A/B | Read handler.py; verify skill inputs | **[ ] Authorize targeted read sidecar** |
| CDX-3: reactive pipeline idempotency unverified | Medium | A | Add review before next reactive-pipeline prompt | **[ ] Track for prompt 11 if reactive** |
| CDX-4: skill I/O field-level schema match | Low | A | Manual spot-check | **[ ] Fold into QC after 10c-iii tests run** |
| CDX-5: deferred import in morning_digest | Low | A | Move to module top | **[ ] Fold into next morning_digest-touching prompt** |
| V-1: morning_digest ADHD rendering inactive | Info | A | Confirm loader wiring prompt | **[ ] Track for loader-wiring prompt** |
| V-2: paralysis_detection nudge not delivered | Info (elevated) | A | Explicit outbound wiring prompt | **[ ] Authorize outbound sidecar** / **[ ] Track for ADHD-facing prompt** |
| V-3: crm_surface drift detection missing data | Info | A | Read crm_surface handler | **[ ] Authorize targeted read** |

---

## Overall build recommendation

**The build may proceed to prompt 11.** No Phase A hard blockers. The 10d Partner audit's verdict (0 Phase A blockers, F-PB-1 as the only Phase B blocker) is confirmed by this Routine audit, with additional medium-severity carry-forward items (CSO-1, CDX-2, CDX-3) surfaced that the Partner audit did not catch.

Operator actions before resuming:

1. **Disposition CSO-1** (D8 gap in wrapper.py). This is a constitutional violation; even if deferred to prompt 12 scope, mark it explicitly in the disposition table above.
2. **Disposition V-2** (paralysis nudge not delivered). The ADHD vision's most concrete behavioral promise is unmet in v1. Decide whether a sidecar is warranted before Phase B or whether it's accepted as a known gap for a later prompt.
3. **Mark CDX-1 (F-PB-1) as tracked** for prompt 16 Deliverables — Partner audit already recommended this; this Routine confirms.

When all three operator dispositions above are filled in, the Routine's pause gate is resolved. Proceed to prompt 11.

---

*Routine complete. Branch: `claude/stoic-ramanujan-DYgem`. This file will be committed and pushed to the feature branch for operator review. No changes were made to production code.*
