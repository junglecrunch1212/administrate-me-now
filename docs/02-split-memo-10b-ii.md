# Tier C split memo — Prompt 10b-ii: Commitment + thank-you reactive pipelines

**Author:** AdministrateMe Build Supervision Partner
**Date:** 2026-04-27
**Disposition:** Secondary-split candidate flagged in `docs/01-split-memo-10b.md` §10b-ii ("Fits one Claude Code session — barely. … 10b-ii itself becomes a candidate for 10b-ii-α / 10b-ii-β. The memo flags this as a watch.")
**Verdict:** SPLIT INTO 10b-ii-α AND 10b-ii-β.

---

## Context

Prompt 10b was split into 10b-i and 10b-ii on 2026-04-26 per `docs/01-split-memo-10b.md`. The sequence-update PR (#37) landed the new sequence-table rows; the build PR (#38) shipped 10b-i (`identity_resolution` + `noise_filtering` pipelines + `classify_message_nature@2.0.0` skill pack + two new event schemas at v1).

10b-ii is now next in queue. The split memo's §10b-ii scope describes:

1. Pipeline pack `commitment_extraction` — full REFERENCE_EXAMPLES.md §2 implementation. Subscribes to `messaging.received` + `messaging.sent` (+ TODO markers for unregistered events). Calls `find_party_by_identifier` for sender resolution. Calls `classify_commitment_candidate` then `extract_commitment_fields`. Emits `commitment.proposed` (existing v1) or `commitment.suppressed` (new v1). Honors per-member dedupe windows.
2. Pipeline pack `thank_you_detection` — subscribes to same. Calls existing `classify_thank_you_candidate@1.3.0` (already on main from 09b). On positive, calls `extract_thank_you_fields`. Emits `commitment.proposed` with `kind=other` + thank-you-decision reasons.
3. Three new skill packs in 09b shape: `classify_commitment_candidate`, `extract_commitment_fields`, `extract_thank_you_fields`.
4. One new event schema at v1: `commitment.suppressed`.
5. Test pyramid: ~35–45 tests across pack-loader canaries + handler-direct unit cases + integration round-trips.

This is intertwined with **UT-12 (parties-DB seam decision)**, opened during 10b-i's QC pass. UT-12 surfaced because `commitment_extraction` cannot ship in 10b-i's degenerate-clean mode — its sender-resolution path is observable behavior that downstream tests inspect, and an always-None sender makes every commitment proposal land with a fabricated owed-to. Three options at orientation:

- **(a)** Thread `parties_conn_factory: Callable[[], sqlcipher3.Connection]` through `PipelineContext` and `PipelineRunner.__init__`. One extra Commit 1-shaped change.
- **(b)** Use 10b-i's injectable-loader pattern for `commitment_extraction` too. Compounds the degenerate-mode debt.
- **(c)** Split 10b-ii into 10b-ii-α (parties-DB seam wiring + `commitment_extraction` + 2 skills) and 10b-ii-β (`thank_you_detection` + `extract_thank_you_fields`).

**Verdict:** option (c). Reasoning below.

---

## Why this overruns one Claude Code session

Empirical baselines:

- **09b** shipped one skill pack: ~660 lines on disk + 8 tests.
- **10a** shipped the pipeline subsystem: ~340-line refactored prompt; 17 tests.
- **10b-i** shipped two pipelines + one skill pack + two event schemas: 320-line prompt; 22 tests; 4 commits. Fit one session **without** new infrastructure.

10b-ii at original scope:

| Component | Files | Tests (est.) |
|---|---|---|
| `commitment_extraction` pipeline pack | 2 | 8–10 |
| `thank_you_detection` pipeline pack | 2 | 5–7 |
| `classify_commitment_candidate` skill pack | 4–5 | 4–6 |
| `extract_commitment_fields` skill pack | 4–5 | 4–6 |
| `extract_thank_you_fields` skill pack | 4–5 | 3–4 |
| `commitment.suppressed` schema at v1 | edit `domain.py` + registry test | 1–2 |
| Integration tests | 1 file | 4–6 |
| **Plus UT-12 (a) infrastructure** if not split | — | +3–5 |

Single-prompt path: ~700–900 lines new code, ~30–40 new tests. Commit 1 alone — schema + parties-DB seam + three full 09b-shape skill packs — is structurally larger than 09b's whole single-skill-pack session. PM-15 was written for exactly this signal. The 07c saga (two timeouts before the α/β split landed) is the cautionary precedent.

UT-12 made the watch flag concrete: option (a) is the architecturally-correct seam choice but adds Commit 1 work that the original sizing did not include. Combined load is empirically over budget.

---

## The split

### 10b-ii-α: Parties-DB seam + `commitment_extraction` + its two skills

**Scope:**

- **Infrastructure (Commit 1, partial):** Extend `PipelineContext` (`adminme/pipelines/base.py`) with `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None` (default None for backward compat with 10a's runner tests). Extend `PipelineRunner.__init__` (`adminme/pipelines/runner.py`) with an optional `parties_conn_factory` keyword arg. Wire it through `_make_callback`. The factory is opened/closed by the pipeline inside `handle()` per the 10a connection-management note — runner does not own per-pipeline DB connections.
- **Schema (Commit 1, completion):** Append `CommitmentSuppressedV1` to `adminme/events/schemas/domain.py` next to existing `CommitmentProposedV1`. Register at v1 per [D7]. Required fields per REFERENCE_EXAMPLES §2: `reason`, `confidence`, `threshold`, `source_event_id`. Use Literal for `reason` (initial enum: `below_confidence_threshold`, `dedupe_hit`, `skill_failure_defensive_default`).
- **Skill pack `packs/skills/classify_commitment_candidate/`** — full 09b shape. Input `{message_text, sender_party_id, receiving_member_id, thread_context}`; output `{is_candidate: bool, confidence: float, reasons: list[str]}`. Defensive default per F-2 carry-forward: `is_candidate: False`.
- **Skill pack `packs/skills/extract_commitment_fields/`** — full 09b shape. Input `{message_text, sender_party_id, receiving_member_id, classify_reasons}`; output `{kind, owed_by_member_id, owed_to_party_id, text_summary, suggested_due, urgency, confidence}`. Output schema must round-trip into `CommitmentProposedV1` without coercion drift.
- **Pipeline pack `packs/pipelines/commitment_extraction/`** — full REFERENCE_EXAMPLES.md §2 implementation. Subscribes to `messaging.received` + `messaging.sent`. TODO markers (per PM-9) for `telephony.voicemail_transcribed`, `calendar.event.concluded`, `capture.note_created`. Resolves sender via `find_party_by_identifier` using `ctx.parties_conn_factory`. Calls classify → if confidence ≥ threshold, calls extract → emits `commitment.proposed`. Below threshold emits `commitment.suppressed`. Dedupe deferred to a future projection-side prompt (TODO). Per-member overrides config-driven via `config.example.yaml` (REFERENCE_EXAMPLES §2 lines 638–663 — keep structure; tenant-specific values stay out of platform code per [§12.4]).
- **Tests (~25–30):** Pack-loader canaries (1 each) + handler-direct unit cases (8–10 for pipeline, 4–6 each for two skill handlers' coercion paths) + 1 integration round-trip with stubbed `run_skill_fn` and tmp parties DB seeded with one party.
- **F-2 honored:** pipeline's exception list includes `SkillSensitivityRefused` and `SkillScopeInsufficient` if either skill declares non-`normal` sensitivity or non-empty `context_scopes_required`. Verify SKILL.md frontmatter at depth-read time. Defensive default = no proposal + `commitment.suppressed` with `reason: skill_failure_defensive_default`.

**Why this group:** infrastructure ships with its first consumer (seam without consumer is dead infrastructure; consumer without seam is the rejected degenerate-clean compromise). The two skills are tightly coupled to `commitment_extraction` — not called by any other pipeline. `thank_you_detection` carves off cleanly because it leans on existing `classify_thank_you_candidate@1.3.0` from 09b and reuses the seam landed here.

**Estimated session size:** ~450–500 lines refactored prompt; ~25–30 tests; 2 new skill packs; 1 pipeline pack; 1 event schema; 1 infrastructure extension across two `adminme/pipelines/` modules. Comparable to 10b-i (320 lines, 22 tests, 1 skill + 2 pipelines) plus one extra-skill-pack worth of work plus the runner extension. Fits one session at the same shape as 10b-i.

**4-commit shape:**

1. `PipelineContext.parties_conn_factory` + `PipelineRunner.__init__` extension + `_make_callback` wiring + runner-test updates + `CommitmentSuppressedV1` at v1 + 2 new skill packs at full 09b shape. Verification: mypy preflight + event-registry test + skill-pack tests.
2. `packs/pipelines/commitment_extraction/` (yaml + handler.py + pack-loader canary + handler-direct unit tests). Verification: pipeline pack tests pass.
3. Defensive-default exception widening (F-2) + per-member-overrides config plumbing + dedupe TODO. Verification: defensive-default tests pass.
4. Integration round-trip + BUILD_LOG + push. Verification: full suite + `verify_invariants.sh` exit 0.

### 10b-ii-β: `thank_you_detection` + `extract_thank_you_fields`

**Scope:**

- **Skill pack `packs/skills/extract_thank_you_fields/`** — full 09b shape. Input `{message_text, sender_party_id, receiving_member_id, classify_reasons}`; output `{recipient_party_id, suggested_text, urgency, confidence}`. Smaller than `extract_commitment_fields` because the binary classification already happened upstream in `classify_thank_you_candidate@1.3.0`.
- **Pipeline pack `packs/pipelines/thank_you_detection/`** — same trigger set as `commitment_extraction` (with same TODO markers). Calls existing 09b `classify_thank_you_candidate@1.3.0`. On positive, calls `extract_thank_you_fields`. Emits `commitment.proposed` with `kind="other"` + reasons referencing the thank-you decision (per BUILD.md §1150, owner-scoped). **Decision deferred to depth-read:** if BUILD.md §1150 implies `thank_you` should be its own kind in `CommitmentProposedV1.kind`, that is a Literal-extension migration (forward-only per [D7]) — flag as the prompt's open question; do NOT silently extend the enum. Default path is `kind="other"`.
- **Reuse:** parties-DB seam already in `PipelineContext` from 10b-ii-α. No new infrastructure.
- **Tests (~12–15):** Pack-loader canary + handler-direct unit cases (5–7 for pipeline) + 3–4 for new skill's coercion paths + 1 integration round-trip.

**Why this group:** once 10b-ii-α lands, the only new work is one pipeline + one skill — analogous to a single 09b-style skill pack plus a small pipeline. `thank_you_detection` depends on `commitment_extraction`'s patterns being on main (defensive-default exception list, per-member overrides config shape, dedupe TODO disposition) — not on its code, but on the patterns being established. Sequencing prevents pattern divergence.

**Estimated session size:** ~250–300 lines refactored prompt; ~12–15 tests; 1 new skill pack; 1 pipeline pack; 0 event schemas; 0 infrastructure changes. Smaller than any single-prompt session shipped to date — cleanly within budget.

**4-commit shape:**

1. `extract_thank_you_fields` skill pack at full 09b shape.
2. `packs/pipelines/thank_you_detection/` (yaml + handler.py + pack-loader canary + handler-direct unit tests).
3. Owner-scope wiring (per BUILD.md §1150 — "James's thank-yous are James's; Laura's are Laura's") via existing GuardedWrite if applicable, or via documented reasons-list convention if owner-scope is a downstream projection concern.
4. Integration round-trip + BUILD_LOG + push.

---

## Dependency impact

- **10b-ii-α depends on:** 10b-i merged (`find_party_by_identifier` returns parties from auto-created entries) + 10a (pipeline runner). Both already merged.
- **10b-ii-β depends on:** 10b-ii-α merged (uses `ctx.parties_conn_factory`; defensive-default pattern; per-member overrides config shape).
- **10c (proactive pipelines) depends on:** 10b-ii-β merged + UT-2 resolved. Original 10c → 10b-ii dependency flips to 10c → 10b-ii-β.
- **10d (checkpoint) depends on:** 10a + 10b-i + 10b-ii-α + 10b-ii-β + 10c merged. Audit scope expands to "four packs + parties-DB seam across three prompts."
- **`scripts/verify_invariants.sh`:** no change. Pipelines remain outside the projection-emit allowlist (PM-14).
- **`PROMPT_SEQUENCE.md`:** existing 10b-ii row replaced by 10b-ii-α and 10b-ii-β. Dependency-graph ASCII updates from `10a → 10b-i → 10b-ii → 10c → 10d` to `10a → 10b-i → 10b-ii-α → 10b-ii-β → 10c → 10d`. Hard-sequential-dependency line gains: "10b-ii-α before 10b-ii-β. Parties-DB seam ships in 10b-ii-α; thank-you pipeline reuses it."
- **`D-prompt-tier-and-pattern-index.md`:** 10b-ii row flips disposition from "Pre-split candidate (watch)" to "Was split on arrival" with sub-prompts beneath. Updated out of band per PM-21 / PM-22 (lives in Partner setup, not in repo).
- **`docs/01-split-memo-10b.md`:** unchanged. This memo is the on-disk record of the secondary split, mirroring the 01- file's role for the primary split.
- **UT-12 RESOLUTION:** option (c) selected via this split. Status flips OPEN → CLOSED upon merge of the sequence-update PR.

---

## Self-check (delivery-gate per E-session-protocol §2.9)

- Split decision made BEFORE drafting a full single-prompt 10b-ii. Init prompt §11.5 satisfied.
- Two sub-prompts each forecast at < 600 lines + < 35 tests + ≤ 4 net-new modules. 10b-ii-α: ~450–500 lines, ~25–30 tests, 4 net-new modules + one infrastructure extension. 10b-ii-β: ~250–300 lines, ~12–15 tests, 2 net-new modules. Both within Claude Code's empirically-known one-session window.
- Dependency chain explicit: 10b-i → 10b-ii-α → 10b-ii-β → 10c.
- `find_party_by_identifier` is on main since 10b-i; `parties_conn_factory` seam is the missing wiring step, sized as one Commit 1-shape change.
- No new event types beyond what BUILD.md / REFERENCE_EXAMPLES name. `commitment.suppressed` lands in 10b-ii-α; thank-you commitments default to `kind="other"` to avoid an enum-migration commit in the same PR as the pipeline build.
- PM-9 honored: subscribes to unregistered events become TODO(prompt-XX) markers in the manifest comment, not sub-prompt fragmentation.
- PM-14 honored: pipelines under `packs/pipelines/`, not `adminme/projections/`. `verify_invariants.sh` `ALLOWED_EMIT_FILES` does not extend.
- PM-15 honored: 10b-ii-α bundles new infrastructure with its first consumer, then 10b-ii-β reuses it — matches the 07c-α/β precedent.
- F-2 carry-forward addressed at depth-read in both sub-prompts.
- UT-12 resolves to (c)+(a) by the split itself: split is option (c); seam wired through `PipelineContext` as 10b-ii-α's Commit 1 is option (a).

**Memo verdict: secondary split approved by Partner self-check.**
