# Split memo — prompt 11 (Adapter framework expansion, 3-way)

**Parent prompt:** `prompts/11-adapter-framework.md` (parent draft, 15 KB).
**Decision date:** 2026-05-16.
**Authored by:** Partner (Cowork chat), Cohort B per `docs/08-architecture-amendment-adapter-rebase.md` §3.2.
**Status:** OPEN — sub-prompt v4 refactors pending operator merge of this memo.

---

## §1 — Why split

`PROMPT_SEQUENCE.md` row 115 explicitly flags the framework prompt as a **pre-split candidate** (5-6 hrs estimated, single session insufficient per `D-prompt-tier-and-pattern-index.md`). The five deliverables decompose along a clean dependency edge:

- The **contract layer** (5 ABCs + manifest extensions) is what everything else builds on.
- The **install-time validator** consumes the manifest contract; it cannot be authored until the contract exists.
- The **CLI subcommand group + authoring guide** consume the validator (`adminme adapters validate` runs it; the authoring guide documents what it catches).

This gives a natural 3-way split. Other shapes were considered:

- **2-way (contract + validator together vs CLI + guide)**: too much in c1, leaves the CLI sub-prompt thin.
- **4-way (one sub-prompt per ABC + one for everything else)**: ABCs share too much infrastructure (manifest extensions, base.py Protocol) to author independently; cohorted ABCs is the natural shape.
- **5-way (one per deliverable)**: over-fragmentation; CLI + guide are tightly coupled to validator; ABCs + manifest extensions are tightly coupled to each other.

Pattern precedent: this matches the `10c → 10c-i / 10c-ii / 10c-iii` split (foundation → consumers → integration), the `10b → 10b-i / 10b-ii-α / 10b-ii-β` split (foundation → split-by-pipeline), and the recently-superseded `11 → 11a/b/c/d` simple-canon split (which had the wrong scope but the right shape principle).

---

## §2 — Split structure

| Sub-prompt | Slug | Scope | Est. commits | Est. spend per cycle |
|---|---|---|---|---|
| **11-α** | `adapter-framework-abcs-and-manifest` | Five abstract base classes in `adminme/lib/adapters/` (`CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter`, `OutboundActionAdapter`) per [PM-34] + manifest format extensions in the pack-registry layer per [PM-33] / [PM-35] (capabilities-as-list, write_capabilities, sensitivity_default, observation_mode_required, owner_scope_overridability_at_install, sharing_model_discriminator, dedup_strategy). Establishes the contract layer. | 4 (c1: 3 ABCs (Communication, ExternalStateMirror, InboundData) + tests, c2: 2 ABCs (PersonalKnowledge, OutboundAction) + tests, c3: manifest extension fields + pack-registry parser + tests, c4: integration tests + BUILD_LOG) | $80-120 |
| **11-β** | `adapter-framework-validator` | Install-time validator extensions per [D20] / memo 05 §3.4. Five checks: capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence + signature. Plugs into pack-install-validate stage (stage 1 of `DIAGRAMS.md` §8). | 4 (c1: capability-runtime coherence + event-schema registration checks, c2: projection-subscription compatibility check + tests, c3: base-class conformance + write_capabilities method-presence checks, c4: integration tests + BUILD_LOG) | $60-100 |
| **11-γ** | `adapter-framework-cli-and-guide` | `adminme adapters` CLI subcommand group per [D20]: `list` / `validate` / `scaffold` (with three-layer developer-mode gate per [D20]) + authoring guide at `docs/adapter-authoring-guide.md`. | 4 (c1: `adminme adapters list` subcommand + tests, c2: `adminme adapters validate <pack>` subcommand + tests, c3: `adminme adapters scaffold <kind> <runtime>` with developer-mode gate + scaffold templates, c4: authoring guide + integration tests + BUILD_LOG) | $40-80 |

**Total estimated cycle cost:** $180-300 across the three sub-prompts. Within PROMPT_SEQUENCE.md row 115's 5-6 hr envelope.

---

## §3 — Sub-prompt dependencies

**STACKED at the cohort level; NOT stacked at the branch level** (each sub-prompt is its own independent v4 prompt with its own 4-commit cycle):

```
11-α (ABCs + manifest)
  ↓ depends-on
11-β (validator)
  ↓ depends-on
11-γ (CLI + guide)
```

**Why 11-α first:** the validator (11-β) reads the manifest extension fields; the CLI's `validate` subcommand invokes the validator. Authoring 11-β without 11-α's contract leaves the validator with nothing to validate. Authoring 11-γ before 11-β leaves the CLI's `validate` subcommand without an implementation to wrap.

**Branch strategy per sub-prompt:** each sub-prompt is STACKED c1→c4 (per `docs/BRANCH-STRATEGY.md`), with PR base = `main` for all c-bands per the PR #106 sidecar default.

---

## §4 — What ships, where

### 11-α deliverables

- `adminme/lib/adapters/__init__.py` (NEW) — re-exports the 5 ABCs.
- `adminme/lib/adapters/communication_base.py` (NEW) — `CommunicationAdapter` ABC. ~120 lines including docstring + Protocol-extension contract.
- `adminme/lib/adapters/external_state_mirror_base.py` (NEW) — `ExternalStateMirrorAdapter` ABC. ~140 lines (asymmetric write_capabilities per UT-27 adds shape).
- `adminme/lib/adapters/inbound_data_base.py` (NEW) — `InboundDataAdapter` ABC. ~100 lines (simplest: inbound-only).
- `adminme/lib/adapters/personal_knowledge_base.py` (NEW) — `PersonalKnowledgeAdapter` ABC. ~120 lines (bridge-runtime defaults; owner-scope-from-Tailscale invariant).
- `adminme/lib/adapters/outbound_action_base.py` (NEW) — `OutboundActionAdapter` ABC. ~130 lines (observation-mode-required default; `action.requested` consumption shape).
- Manifest format extensions:
  - `bootstrap/pack_registry/manifest_schema.py` (or wherever pack-load lives on main — verify at c1 start) — extend to parse `capabilities` as a list of capability blocks, `write_capabilities`, `sensitivity_default`, `observation_mode_required`, `owner_scope_overridability_at_install`, `sharing_model_discriminator`, `dedup_strategy`.
  - `bootstrap/pack_registry/manifest_models.py` (or equivalent) — Pydantic models for the new fields.
- Tests:
  - `tests/unit/lib/adapters/test_communication_adapter_base.py` (~10 tests: Protocol contract, required methods, observation-mode hook).
  - `tests/unit/lib/adapters/test_external_state_mirror_adapter_base.py` (~12 tests including asymmetric write_capabilities surface).
  - `tests/unit/lib/adapters/test_inbound_data_adapter_base.py` (~8 tests).
  - `tests/unit/lib/adapters/test_personal_knowledge_adapter_base.py` (~10 tests).
  - `tests/unit/lib/adapters/test_outbound_action_adapter_base.py` (~10 tests).
  - `tests/unit/pack_registry/test_manifest_extensions.py` (~12 tests: each new field + capability-as-list + per-capability sensitivity overrides).

Test floor: ≥60 across the four commits.

### 11-β deliverables

- `bootstrap/pack_registry/validator/capability_runtime_coherence.py` (NEW) — Cat-A bridge = warn; Cat-D + bridge + owner_scope_overridability_at_install: true = hard refusal.
- `bootstrap/pack_registry/validator/event_schema_registration.py` (NEW) — every event family in `capabilities[].event_families` has a registered schema.
- `bootstrap/pack_registry/validator/projection_subscription_compatibility.py` (NEW) — Cat-B / Cat-D event families subscribe to at least one projection.
- `bootstrap/pack_registry/validator/base_class_conformance.py` (NEW) — adapter class inherits from one of the 5 ABCs; category matches manifest.
- `bootstrap/pack_registry/validator/write_capabilities_check.py` (NEW) — per-operation method-presence + signature.
- `bootstrap/pack_registry/validator/__init__.py` (NEW) — `validate_adapter_pack(pack_dir) -> ValidationResult` aggregator.
- Plug into stage 1 of the pack-install lifecycle (verify the lifecycle path at c1 start).
- Tests: ≥30 across the four commits (5 checks × 4-6 happy-path + bad-path scenarios each).

### 11-γ deliverables

- `adminme/cli/commands/adapters.py` (or wherever CLI commands live — verify at c1 start) — adds the `adapters` subcommand group with `list`, `validate`, `scaffold`.
- `adminme/cli/commands/adapters_scaffold/` — scaffold-template directory tree:
  - `templates/cat-a-central/` (Gmail-shape skeleton).
  - `templates/cat-b-central/` and `templates/cat-b-dual-deployment/`.
  - `templates/cat-c-central/`.
  - `templates/cat-d-bridge/`.
  - `templates/cat-e-central/`.
  - Each template includes `pack.yaml`, `adapter.py` (skeleton inheriting from the appropriate ABC), `config.schema.json`, `tests/test_<name>_adapter_pack_internal.py` (F-3 rename).
- `docs/adapter-authoring-guide.md` (NEW) — operator/developer-facing tutorial per the parent draft §"Authoring guide" outline.
- Three-layer developer-mode gate on `scaffold`: `ADMINME_DEVELOPER_MODE=1` env + `--allow-scaffold` flag + interactive confirmation.
- Tests: ≥20 across the four commits.

Total framework prompt test floor: ≥110 across all three sub-prompts.

---

## §5 — Constitutional citations

- [D19] adapter framework (5 base classes + manifest extensions)
- [D20] `adminme adapters scaffold` developer-mode gate
- [PM-31] dedup_strategy field per Cat-B adapter manifest
- [PM-33] manifest format extensions
- [PM-34] 5 abstract base classes in `adminme/lib/adapters/`
- [PM-35] capabilities-as-list (multi-capability adapters)
- UT-26 capabilities-as-list per-capability category declarations
- UT-27 asymmetric write-capabilities (per-operation, not boolean)
- Amendment-2 memo: `docs/05-architecture-amendment-personal-data-layer.md` §1-§3
- Knowledge vaults / member bridges memo: `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`
- Parent draft: `prompts/11-adapter-framework.md` (just merged via PR #114)
- Rebase plan: `docs/08-architecture-amendment-adapter-rebase.md`
- Branch strategy: `docs/BRANCH-STRATEGY.md`
- §2.9 delivery-gate caps: enforced by `docs/PARTNER-PRE-AUTHORSHIP-CHECKLIST.md` self-check.

---

## §6 — Stop / next move

This memo documents the split decision. Operator merges this PR (`partner/cohort-b-split-memo`) to land the memo on `main`.

After merge, Partner authors:

1. `prompts/11-alpha-adapter-framework-abcs-and-manifest.md` — parent draft for 11-α (likely ~6-8 KB).
2. `prompts/_active/11-alpha-adapter-framework-abcs-and-manifest.md` — v4 ten-section refactor.

Once the 11-α refactor PR merges, James fires `executor-cycle.yml` with `prompt_id=11-alpha`. After 11-α's 4 c-band PRs merge, repeat for 11-β, then 11-γ.

**Pause rule (carry-forward from memo 08 §4):** no `executor-cycle.yml` fires with `prompt_id` in the 11* family OTHER THAN the framework sub-prompts (`11-alpha`, `11-beta`, `11-gamma`) until the framework lands. Cohort C retrofit + Cohort D resumed canon both depend on this.

Estimated wall-clock from this memo → framework complete:

- Partner authoring of 11-α draft + v4 refactor: 30-45 min (Cowork chat).
- Operator review + merge of 11-α refactor PR: 5-10 min.
- Executor cycle for 11-α: 80-100 min, $80-120 API spend.
- Operator review + merge of 11-α's 4 c-band PRs: 10-20 min.
- Repeat for 11-β: ~120 min including authorship.
- Repeat for 11-γ: ~100 min including authorship.

Total Cohort B wall-clock: ~6-8 hours operator-attended (mostly waiting on executor cycles).

After Cohort B completes, Cohort C (retrofit) and Cohort D (resumed canon) begin per memo 08 §3.3-§3.4.
