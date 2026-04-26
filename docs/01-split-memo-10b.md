# Tier C split memo — Prompt 10b: Reactive pipelines

**Author:** AdministrateMe Build Supervision Partner
**Date:** 2026-04-26
**Disposition:** Pre-split candidate (per `D-prompt-tier-and-pattern-index.md` v3 row for 10b)
**Verdict:** **SPLIT INTO 10b-i AND 10b-ii.**

---

## Context

Prompt 10a (PR #33, merged 2026-04-26) shipped the pipeline runner: `Pipeline` Protocol, `PipelineContext`, `PipelinePackLoadError`, `LoadedPipelinePack`, `load_pipeline_pack()`, `PipelineRunner.{register,discover,start,stop,status}()`. Reactive-only — proactive packs are skipped during `discover()` per UT-2 carve-out.

Prompt 10b is now queued. The on-disk draft (`prompts/10b-reactive-pipelines.md`, 26 lines) lists **four reactive pipelines** to ship in one Claude Code session:

1. `identity_resolution`
2. `noise_filtering`
3. `commitment_extraction`
4. `thank_you_detection` (called `thank_you` in the draft, but BUILD.md §L4 line 1150 names it `thank_you_detection` — refactor will use the BUILD.md name)

Each pipeline ships with: a `pipeline.yaml` manifest, a `handler.py` implementing the `Pipeline` Protocol, fixture-driven tests, integration coverage against the live runner.

It also asks Claude Code to ship **three new skill packs** (`classify_commitment_candidate`, `extract_commitment_fields`, `extract_thank_you_fields`) following the 09b shape — each with `pack.yaml`, `SKILL.md`, two JSON schemas, prompt.jinja2, optional `handler.py`, and a unit-tests-the-pack file.

It also asks for **new event-schema registrations**: `identity.merge_suggested`, `messaging.classified` (BUILD.md §L4 line 1136 — the original draft mis-named this `noise.filtered`), and `commitment.suppressed`. (`commitment.proposed` already exists per `adminme/events/schemas/domain.py:194`.)

---

## Why this overruns one Claude Code session

Sizing each component against the empirical norms set by 09b (single skill pack: ~660 lines on disk + 8 tests across two files) and 10a (pipeline subsystem: 3 modules + 13 unit tests + 4 integration tests + 2 fixture packs = ~340 lines refactored prompt):

| Component | Files added | Tests added | Approx. work |
|---|---|---|---|
| `identity_resolution` pipeline pack | 2 (yaml + handler) | ~6–8 | half of 10a-equivalent lifecycle work |
| `noise_filtering` pipeline pack | 2 | ~5–7 | similar |
| `commitment_extraction` pipeline pack | 2 + config (REFERENCE_EXAMPLES §2 has thresholds + per-member overrides) | ~8–12 | bigger — has dedupe + per-member thresholds |
| `thank_you_detection` pipeline pack | 2 | ~4–6 | smaller — leans on existing 09b skill |
| `classify_commitment_candidate` skill pack | 4–5 (yaml + SKILL.md + 2 schemas + prompt.jinja2 + handler.py) | 4–6 | full 09b-shape pack |
| `extract_commitment_fields` skill pack | 4–5 | 4–6 | full 09b-shape pack |
| `extract_thank_you_fields` skill pack | 4–5 | 3–4 | full 09b-shape pack (smaller schema) |
| `classify_message_nature` skill pack | 4–5 | 3–4 | full 09b-shape pack (BUILD.md §1136 names it `classify_message_nature@v2`) |
| New event schemas (3) | edits to `crm.py` + `domain.py` (or new module) + tests | ~3 | small but mandatory |
| Integration tests across the four pipelines | 1 file | ~4–6 | each pipeline gets at least one round-trip via the runner |
| BUILD_LOG entry, ruff/mypy/verify | — | — | per universal preamble |

**Estimated total:** ~700–900 lines of new code under `packs/` + `adminme/events/schemas/` + `tests/`, plus ~50–80 new tests across ~10–12 test files.

10a was the pipeline subsystem itself and produced ~17 tests on a clean week. 09b produced one skill pack (8 tests). 10b in single-prompt form bundles roughly 4 × pipeline-pack-with-tests + 4 × skill-pack-with-tests + schema additions + integration suite — at least 4× the per-session ceiling we've hit so far. PM-15 was introduced for exactly this signal: a draft that asks Claude Code to land both **four new pipelines** AND **the four skill packs they call** in one session. The 07c-α/β split is the empirical precedent (two prior single-session attempts died partway through).

The original on-disk 10b draft also rolls four skill packs into a single line — "Create the commitment skills in this prompt following the §3 pattern" — without acknowledging that each skill pack in the 09b shape is its own ~700-line + 8-test deliverable. PM-8 (inline implementation code in prompts is a warning sign) applies here: the draft's terseness hides the actual scope.

---

## The split

### 10b-i: Identity resolution + noise filtering

**Scope:**
- Pipeline pack `packs/pipelines/identity_resolution/` — subscribes to `messaging.received`, `messaging.sent`, `telephony.sms_received`. On unknown `from_identifier`, computes similarity against existing identifiers; above 0.85 emits `identity.merge_suggested`; otherwise emits `party.created` + `identifier.added`. Never auto-merges. **Heuristic-only — no skill calls.** All scoring is local (Levenshtein on display names, domain tail comparison for emails, exact-prefix check for phones).
- Pipeline pack `packs/pipelines/noise_filtering/` — subscribes to `messaging.received`, `telephony.sms_received`. Calls `classify_message_nature@v2` skill. Emits `messaging.classified` with classification + confidence + skill version.
- Skill pack `packs/skills/classify_message_nature/` — full 09b shape; classifies as `noise | transactional | personal | professional | promotional`. (BUILD.md §1136.)
- Two new event schemas at v1: `identity.merge_suggested` (in `crm.py` next to `party.merged`) and `messaging.classified` (new module `adminme/events/schemas/classification.py` OR appended to `ingest.py` — depth-read decides).
- Tests: pipeline pack tests via `load_pipeline_pack()` + handler-direct unit cases; skill pack tests via `load_pack()` + handler-direct unit cases per the 09b test pattern; one integration test per pipeline against the live runner.

**Why this group:** both pipelines are L1-input consumers (`messaging.received` / `telephony.sms_received`); identity_resolution is heuristic-only (no skill dependency); noise_filtering depends on exactly one new skill pack. They form a self-contained "first wave" of inbound classification before commitment work begins.

**Estimated session size:** ~450 lines refactored prompt; ~30–35 tests; one new skill pack; two pipeline packs; two event schemas. Comparable to 09b's single-skill-pack session plus ~one extra pipeline's worth of integration tests. Fits one Claude Code session.

### 10b-ii: Commitment extraction + thank-you detection

**Scope:**
- Pipeline pack `packs/pipelines/commitment_extraction/` — full REFERENCE_EXAMPLES.md §2 implementation: subscribes to `messaging.received` + `messaging.sent` + `telephony.voicemail_transcribed` (defer unregistered subscribes; sub TODO(prompt-XX) for unregistered events) + others; uses `find_party_by_identifier` for sender resolution; calls `classify_commitment_candidate` then `extract_commitment_fields`; emits `commitment.proposed` (existing v1) or `commitment.suppressed` (new v1).
- Pipeline pack `packs/pipelines/thank_you_detection/` — subscribes to same; calls existing `classify_thank_you_candidate@1.3.0` (already on main from 09b); on positive, calls `extract_thank_you_fields`; emits `commitment.proposed` with `kind=other` + reasons referencing the thank-you decision (or extends the kind enum if depth-read reveals it must — handled in 10b-ii's refactor session, not this memo).
- Skill packs `classify_commitment_candidate/`, `extract_commitment_fields/`, `extract_thank_you_fields/` — full 09b shape.
- One new event schema at v1: `commitment.suppressed` (in `domain.py` next to `commitment.proposed`).
- Tests as in 10b-i.

**Why this group:** both pipelines call multi-skill chains and emit `commitment.proposed`; they share dedupe semantics (REFERENCE_EXAMPLES.md §2 dedupe_window_hours); they depend on identity_resolution being live (which 10b-i ships). Putting them after 10b-i's merge means commitment extraction can `find_party_by_identifier` on senders that 10b-i's pipeline has already proposed.

**Estimated session size:** ~500 lines refactored prompt; ~35–45 tests; three new skill packs; two pipeline packs; one event schema. Fits one Claude Code session — barely. If depth-read at refactor time reveals it's still too big, 10b-ii itself becomes a candidate for 10b-ii-α (commitment_extraction + its two skills) / 10b-ii-β (thank_you_detection + extract_thank_you_fields). The memo flags this as a watch.

---

## Dependency impact

- **10b-i depends on:** 10a (merged). No new dependencies.
- **10b-ii depends on:** 10b-i merged (so identity_resolution is live and `find_party_by_identifier` returns parties from auto-created entries). Also depends on 09b's `classify_thank_you_candidate@1.3.0` (already on main).
- **10c (proactive pipelines) depends on:** 10b-i and 10b-ii both merged, plus UT-2 resolved. No change.
- **10d (checkpoint) depends on:** 10a + 10b (full) + 10c merged. The 10b checkpoint scope expands to audit four packs across two prompts; trivial.
- **`scripts/verify_invariants.sh`:** no change. ALLOWED_EMITS / ALLOWED_EMIT_FILES are projection-emit allowlists; pipelines are not subject to that allowlist (PM-14).
- **`packs/pipelines/`:** new top-level subdir under `packs/`. 10b-i creates it (no fresh-instance bootstrap impact; 16 wires it).
- **TODO marker in `adminme/projections/interactions/handlers.py:15`:** "noise_filtering pipeline will merge related interactions into one row." This TODO is **out of scope for both 10b-i and 10b-ii** — interaction aggregation happens in the projection (post-classification), not in the pipeline. The TODO stays for a future projection-side prompt.

---

## Carry-forward updates needed

After 10b-i merges, partner_handoff.md gets an entry:
> **10b-i (PR #<N>, merged <date>) — identity_resolution + noise_filtering pipelines + classify_message_nature skill pack + identity.merge_suggested + messaging.classified event schemas at v1.**

After 10b-ii merges, partner_handoff.md gets:
> **10b-ii (PR #<N>, merged <date>) — commitment_extraction + thank_you_detection pipelines + classify_commitment_candidate + extract_commitment_fields + extract_thank_you_fields skill packs + commitment.suppressed event schema at v1.**

`PROMPT_SEQUENCE.md`'s sequence table gets two new rows; the existing `10b` row is deleted. `D-prompt-tier-and-pattern-index.md`'s 10b row flips disposition from "Pre-split candidate" to "Was split on arrival" with the two sub-prompts listed beneath.

---

## What James does next

1. Read this memo, approve or push back on the split shape.
2. If approved: paste the **sequence-update micro-prompt** (`02-claude-code-sequence-update.md` in this package) into a Claude Code session. Claude Code lands a single-purpose PR updating `prompts/PROMPT_SEQUENCE.md` and `prompts/D-prompt-tier-and-pattern-index.md` (note: `D-prompt-tier-and-pattern-index.md` is in Project knowledge / Partner setup, NOT in the repo — see micro-prompt for handling). After merge, James updates the `D-` file in the Partner setup package out of band per PM-21.
3. Paste the **refactored 10b-i prompt** (`03-prompt-10b-i-identity-and-noise.md`) into a fresh Claude Code session prefixed with the universal preamble.
4. After 10b-i merges: run a Type 1 Partner session — QC of 10b-i + refactor 10b-ii.

---

## Self-check (delivery-gate per E-session-protocol.md §2.9)

- ✅ Split decision made BEFORE drafting a full single-prompt 10b. Init prompt §11.5 satisfied.
- ✅ Two sub-prompts each forecast at < 600 lines + < 50 tests + ≤ 4 new files of similar shape. Within Claude Code's empirically-known one-session window.
- ✅ Dependency chain explicit: 10b-i → 10b-ii. No silent cross-references.
- ✅ Hot-path discipline: `find_party_by_identifier` already on main per `adminme/projections/parties/queries.py:47`; pipelines consume it without new infrastructure.
- ✅ No new event types introduced beyond what BUILD.md §L4 actually names (`messaging.classified`, `identity.merge_suggested`, `commitment.suppressed`). The original draft's `noise.filtered` was wrong against BUILD.md and has been corrected.
- ✅ PM-9 honored: pipelines that subscribe to events not yet registered (`telephony.voicemail_transcribed`, `calendar.event.concluded`, `capture.note_created`) get TODO(prompt-XX) in the manifest's `triggers.events` comment, NOT a sub-prompt fragmentation.
- ✅ PM-14 honored: pipelines live under `packs/pipelines/`, not `adminme/projections/`. Verify-invariants ALLOWED_EMIT_FILES does not extend.

**Memo verdict: split approved by Partner self-check. Awaiting James's go.**
