# Architecture amendment 08 — Adapter framework rebase

**Author:** AdministrateMe Build Supervision Partner (Cowork chat).
**Date:** 2026-05-16.
**Disposition:** Architecture-amendment-acknowledgment. Recognizes that 11a/11b/11c executor cycles were authored against the wrong canon. Declares a staged path back to the canonical scope defined by `prompts/PROMPT_SEQUENCE.md` + memos 04 (`docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`) + 05 (`docs/05-architecture-amendment-personal-data-layer.md`).
**Verdict:** **REBASE.** Shipped artifacts are not reverted; they are retrofitted into the canonical framework via a follow-up cohort.
**Authority:** James (operator), 2026-05-16, this session — “B you idiot. Get it back on track.”
**Severity:** High. Five executor cycles + four refactored prompts were authored against a partial subset of the architecture. The shipped code IS sound at the level it operates, but the surfaces are wrong (event taxonomy, base classes, runtime model, owner-scope discipline, sharing-model discriminator).

---

## 1. What happened

### 1.1 The drift

Between 2026-05-15 and 2026-05-16, Partner authored four refactored prompts (`11a-adapter-foundation-gmail`, `11b-adapter-plaid`, `11c-adapter-reminders-bidirectional`, `11d-adapter-calendars`) and the operator shipped three executor cycles (11a/b/c) under what Partner repeatedly framed as the “simple split memo” 4-way canon (`docs/06-split-memo-11.md`).

The actual canon — on main, in `prompts/PROMPT_SEQUENCE.md` rows 115-123 — is the **expanded 5-abstract-base-class adapter framework** introduced by memos 04 and 05 ([D19]–[D25], [PM-33]–[PM-35], [PM-29], [PM-31]). PROMPT_SEQUENCE.md has had this expanded layout the entire time. The simple split memo was a partial scope derivation that should have been ratified through an amendment cycle before being treated as canon; it never was.

Partner repeatedly ran a “Gate 0 surface-sync check” during 11b/c/d authorship and reported PASS. Those checks were structurally hollow: they compared workspace pinned content vs main path-by-path but never compared the workspace + main framings of the 11* row contents. The “Option C disposition” (PR #98 memo 07) framed the expanded framework as “deferred future work” — it was not. It is the canon. Memo 07's framing is now corrected via this PR (amended header redirects to this memo).

### 1.2 The mechanism

The pre-authorship gate (`docs/PARTNER-PRE-AUTHORSHIP-CHECKLIST.md`) Gate 0 was added in PR #97 to prevent exactly this class of failure. It works as written for path-level drift. It does not work for **architectural-scope drift** — the case where every cited path exists on main AND the prompt-level scope target disagrees with main's canonical sequence. The 11b/c/d authorship sessions verified every cited path existed and authored against the wrong scope anyway.

The fix to the gate itself is out of scope for this memo; see §4 followup.

### 1.3 What's actually shipped on main (2026-05-16)

| Artifact | What it is | Canonical category | Notes |
|---|---|---|---|
| `adminme/adapters/base.py` | Simple `Adapter` Protocol (5 KB) | base — not one of the 5 ABCs from memo 05 §3.2 | Salvageable. 5 ABCs in `adminme/lib/adapters/` will extend this. |
| `adminme/events/schemas/adapters.py` | 3 cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) at v1 | framework-level | Fully salvageable; these stay. |
| `adminme/daemons/adapter_supervisor.py` | Supervisor with backoff + restart | framework-level | Salvageable. Needs evolution to handle capability-as-list adapters per [PM-35] but core lives. |
| `adminme/events/schemas/financial.py` | `money_flow.plaid_synced` v1 | Cat-C reference impl event | Salvageable; this is the correct Cat-C event. |
| `adminme/events/schemas/reminders.py` | `reminder.list_item.synced` v1 (single event with `direction` field) | **WRONG taxonomy** | Per [D18] + memo 05 §1.2 Cat-B, the canonical event family is `list.added/updated/deleted` + `list_item.added/updated/completed/deleted`. The shipped single-event-with-direction shape is the drift. Retrofit needed. |
| `packs/adapters/messaging/gmail/` | Full Gmail pack | Cat-A `CommunicationAdapter` | Salvageable; retrofit to inherit `CommunicationAdapter`, declare `capabilities: [Cat-A central]`, add `sharing_model` discriminator, etc. |
| `packs/adapters/financial/plaid/` | Full Plaid pack | Cat-C `InboundDataAdapter` | Salvageable; retrofit to inherit `InboundDataAdapter`. |
| `packs/adapters/reminders/apple-eventkit/` | Bidirectional Reminders pack (~43 KB adapter.py + never-sync predicate) | Cat-B `ExternalStateMirrorAdapter` | **Worst drift.** Needs: (a) inherit `ExternalStateMirrorAdapter`, (b) switch event taxonomy to `list.*` / `list_item.*` per [D18], (c) become **dual-deployment** per [D22] (central on CoS Apple ID + bridge variant per member Apple ID), (d) declare asymmetric write-capabilities per-operation per memo 05 §2.3, (e) add `sharing_model` discriminator. Roughly a full rewrite of the public surface; the EventKit + never-sync internals are salvageable. |
| `prompts/_active/11a-adapter-foundation-gmail.md` | Refactored simple-canon 11a | wrong canon — simple 11a foundation ≠ PROMPT_SEQUENCE.md 11a (Gmail + Plaid together) | Stays on main as historical record; not re-fired. |
| `prompts/_active/11b-adapter-plaid.md` | Refactored simple-canon 11b | wrong canon — simple Plaid alone ≠ PROMPT_SEQUENCE.md 11b (Cat-B cohort) | Stays on main as historical record; not re-fired. |
| `prompts/_active/11c-adapter-reminders-bidirectional.md` | Refactored simple-canon 11c | wrong canon | Stays on main as historical record; not re-fired. |
| `prompts/_active/11d-adapter-calendars.md` | Refactored simple-canon 11d (Google Calendar + CalDAV) | **wrong canon AND wrong artifact** — 11d per PROMPT_SEQUENCE.md is the `member_knowledge` projection, not adapters; CalDAV is removed per [D25] | **DELETED in this PR.** Never executed; safe to remove. |
| `docs/06-split-memo-11.md` | Split memo from Partner session, 2026-05-15 | superseded | Kept on main as historical record. Header amendment in a follow-up PR to redirect to memo 08. |
| `docs/07-deferred-adapter-framework-expansion.md` | Mis-framed memo treating canon as “deferred” | **REFRAMED in this PR** | Header amendment redirects to memo 08. The body content is still useful as a synopsis of the canonical framework. |

### 1.4 What's still in flight (not on main)

None, as of this memo. PR #112 (the wrong-canon 11d refactor) merged at 13:37 UTC; the 11d executor cycle was never fired (caught before bleeding further). The deleted file in this PR removes the artifact entirely.

---

## 2. Canon, restated

Per `prompts/PROMPT_SEQUENCE.md` rows 115-123 + memos 04 + 05:

- **Prompt 11** — framework expansion. Five Python abstract base classes per [PM-34] in `adminme/lib/adapters/`: `CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter`, `OutboundActionAdapter`. Manifest format extensions per [PM-33] / [PM-35] (capabilities-as-list, write-capabilities, sensitivity defaults, `observation_mode_required`, owner-scope-overridability, `sharing_model` discriminator). Install-time validator. `adminme adapters` CLI subcommand group (list, validate, scaffold). `docs/adapter-authoring-guide.md`. Tier: introduction.
- **Prompt 11a** — Gmail (Cat-A `CommunicationAdapter`) + Plaid (Cat-C `InboundDataAdapter`), both central runtime. Reference implementations for two of the five ABCs.
- **Prompt 11b** — Cat-B cohort: Apple Reminders dual-deployment + Apple Calendar dual-deployment + Google Calendar central + Google Tasks central. CalDAV REMOVED per [D25]. All emit `list.*` / `list_item.*` (Reminders, Tasks) or `calendar_event.*` (Calendars).
- **Prompt 11c-i** — L1-bridge daemon (`bridge/adminme_bridge/`) + central `:3337 bridge` ingest endpoint per [D17] + `bridge.enrolled@v1` schema.
- **Prompt 11c-ii** — Cat-D personal-knowledge adapters (Apple Notes prose, Voice Notes, Obsidian) + Apple Notes-checklists Cat-B writeback half (multi-capability per [PM-35]).
- **Prompt 11d** — `member_knowledge` projection (12th projection per [D19]). NOT adapters.
- **Prompt 11e** — Contacts adapters: Apple Contacts (bridge per-member) + Google Contacts (central) per [D23].
- **Prompt 11f** — `lists` projection (13th projection per [D18]).
- **Prompt 11g** — Home Assistant adapter, Cat-C + Cat-E multi-capability per [D24]. Reference implementation for `OutboundActionAdapter`.

Dependencies: 11 → 11a / 11b / 11c-i / 11e / 11g (all flow from the framework). 11c-i → 11c-ii → 11d. 11f after 11b (lists projection needs Cat-B emissions to consume).

---

## 3. Staged recovery plan

Three sequential PR cohorts, each scoped to a single concern. None of the shipped 11a/b/c code is reverted; it is retrofitted under the proper base classes.

### 3.1 Cohort A — Acknowledge + clean up (THIS PR)

- DELETE `prompts/_active/11d-adapter-calendars.md` (never executed; wrong-canon artifact).
- NEW `docs/08-architecture-amendment-adapter-rebase.md` (this file).
- UPDATE `docs/07-deferred-adapter-framework-expansion.md` header to redirect to memo 08.
- Followups noted in §4 (not done in this PR): build_log entry, split-memo-11 header amendment, PROMPT_SEQUENCE.md row reaffirmation (no-op, since it's already correct), pre-authorship-checklist Gate 0 enhancement to catch architectural-scope drift.

Goal: stop the bleeding, acknowledge the drift, make the recovery plan auditable.

### 3.2 Cohort B — Author the missing framework prompt

The canonical sequence references `prompts/11-adapter-framework.md` (PROMPT_SEQUENCE.md row 115). That file does not exist on main — only `prompts/11-standalone-adapters.md` (a different parent draft from a prior iteration) does.

- AUTHOR `prompts/11-adapter-framework.md` (parent draft — the canonical 5-6 hr description per memo 05 §3).
- AUTHOR `prompts/_active/11-adapter-framework.md` (v4 refactor at the cap per `docs/PARTNER-PRE-AUTHORSHIP-CHECKLIST.md`). Pre-split candidate — 5 base classes + manifest spec + validator + CLI + authoring guide is too much for one session.

Goal: land the framework prompt that the rest of the 11* cohort depends on.

### 3.3 Cohort C — Retrofit prompts for shipped 11a/b/c artifacts

Three narrow retrofit prompts that bring shipped 11a/b/c adapter code into the new framework:

- `prompts/_active/11r-a-retrofit-gmail-to-communication-adapter.md` — retrofit Gmail to inherit `CommunicationAdapter` + declare capabilities + add `sharing_model` discriminator. ~2 hr; small surface change.
- `prompts/_active/11r-b-retrofit-plaid-to-inbound-data-adapter.md` — retrofit Plaid to inherit `InboundDataAdapter`. ~2 hr.
- `prompts/_active/11r-c-retrofit-reminders-to-cat-b.md` — the heavy one. Retrofit the Apple Reminders pack to: (a) inherit `ExternalStateMirrorAdapter`, (b) switch event taxonomy from `reminder.list_item.synced` to `list.*` / `list_item.*` per [D18], (c) make it dual-deployment-ready per [D22] (central + bridge configs), (d) declare asymmetric write-capabilities per-operation. ~5-6 hr. Includes a schema migration (deprecate `reminder.list_item.synced` v1; add `list.added/updated/deleted` + `list_item.added/updated/completed/deleted` v1; emit deprecation warning if downstream consumers still subscribe to the old type).

Goal: shipped adapters speak the canonical framework's contracts before any further canonical-sequence prompts touch them.

### 3.4 Cohort D — Resume the expanded canon

In order per PROMPT_SEQUENCE.md row dependencies:

- Re-author 11a as a sub-prompt that ADDS Plaid to the existing Gmail (since both are needed together per row 116). Or treat the shipped Gmail-only 11a as “done” and ship Plaid as a sibling under retrofit cohort C — decision deferred to author-time.
- 11b (Cat-B cohort): 4-way split candidate per memo 05 — likely 11b-i Apple Reminders dual + 11b-ii Apple Calendar dual + 11b-iii Google Calendar + 11b-iv Google Tasks.
- 11c-i (bridge daemon + ingest).
- 11c-ii (Cat-D adapters + Notes-checklists writeback).
- 11d (`member_knowledge` projection).
- 11e (Contacts adapters, Apple + Google).
- 11f (`lists` projection).
- 11g (Home Assistant).

Goal: 11 cohort lands at canonical scope. Estimated 33-43 hours across 9 sub-prompts per the deferred memo's accounting.

### 3.5 Pause point

After cohort A merges and before cohort B begins, the operator confirms the rebase plan is acceptable. If the operator wants a different cohort ordering (e.g., land cohort D's 11d projection first to unblock Cat-D adapters), this memo's §3 ordering is advisory and can be revised.

---

## 4. Followups (not done in this PR)

- **`docs/build_log.md`** — append a Cohort-A entry summarizing the drift acknowledgment. 126 KB file; deferred to a sidecar PR to keep this PR small.
- **`docs/06-split-memo-11.md`** — header amendment redirecting to memo 08. The split memo's content remains historically accurate as a snapshot of the simple-canon framing that was active during 11a/b/c authoring; it is NOT deleted.
- **`prompts/PROMPT_SEQUENCE.md`** — no change needed; the canonical layout in rows 115-123 is already correct. The drift was Partner's failure to honor it, not the file's content.
- **`docs/PARTNER-PRE-AUTHORSHIP-CHECKLIST.md`** — Gate 0 enhancement to catch architectural-scope drift, not just path-level drift. Concretely: after the path-level Gate 0 comparison, also surface the full prompt-row text from workspace vs main PROMPT_SEQUENCE.md side-by-side; require explicit operator confirmation that the FRAMING matches, not just that the paths exist. Add as a v4.4 update.
- **`docs/DECISIONS.md`** — add a [D26] entry documenting the rebase decision (constitutional record).
- **Pause on adapter executor cycles** — until cohort B (framework prompt) lands, no `executor-cycle.yml` fires with a `prompt_id` in the 11* family. Operator-side enforcement; this memo notes the rule.

---

## 5. What's preserved

The shipped 11a/b/c work is **not wasted.** The adapter packs, the cross-cutting schemas, the supervisor, the never-sync predicate — all stay on main. They get retrofitted into the canonical framework via cohort C. The retrofit cost is bounded: ~10 hours across three narrow prompts.

The broader cost of the drift is:

- ~$200 of executor cycle spend on prompts that should have been authored differently (the c2/c3/c4 work especially — c1 schemas + supervisor are fully salvageable).
- ~5 hours of Partner authorship time on the four refactored prompts plus the supporting sidecars (PRs #88, #97, #98, #106, #107, #112) — PR #97 (constitution-sync rule) and PR #106 (stacked-merge anti-pattern fix) remain useful regardless of the rebase; PR #88 (split memo) is superseded by this memo; PR #98 (deferred memo 07) is reframed by this memo; PR #112 (the wrong-canon 11d) is being unwound by this PR.

Net cost of the drift, accepting the rebase: about a half-day of work and the equivalent of one executor cycle that needs to be re-shaped. Cheap by the standards of architectural drift discovered post-merge — the alternative (continuing to build under the simple canon and discovering the drift after 11g) would have been ~3x worse.

---

## 6. Constitutional citations preserved here for future reference

- [D17] proactive pipelines do not write projections directly
- [D18] Apple Reminders dual-deployment + `list.*` event family
- [D19] adapter framework (5 base classes + manifest extensions)
- [D20] `adminme adapters scaffold` developer-mode gate
- [D21] Twilio SMS recategorized as Cat-E
- [D22] Apple Calendar dual-deployment per Amendment-2
- [D23] Contacts adapters (Apple + Google) per Amendment-2
- [D24] Home Assistant multi-capability adapter
- [D25] CalDAV removal (Apple Calendar covers iCloud; Google Calendar covers Google)
- [D26] (new, deferred to a separate DECISIONS.md PR) — rebase decision recorded
- [PM-29] `bridge/adminme_bridge/` daemon path
- [PM-31] dedup_strategy field per Cat-B adapter manifest
- [PM-33] manifest format extensions
- [PM-34] 5 abstract base classes in `adminme/lib/adapters/`
- [PM-35] capabilities-as-list (multi-capability adapters)
- Amendment-2 memo: `docs/05-architecture-amendment-personal-data-layer.md`
- Knowledge vaults / member bridges memo: `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`
- Superseded simple-canon memos: `docs/06-split-memo-11.md`, `docs/07-deferred-adapter-framework-expansion.md` (this PR amends 07's framing)
