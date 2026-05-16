# Prompt 11c: Apple Reminders bidirectional adapter

**Phase:** Phase A — L1 ingest adapters, third sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`.

**Branch:** `claude/11c-<auto>` (harness-assigned). PR base = `main` for all four c-bands (per PR #106 sidecar). STACKED across c1→c4 per §8.5.

**Depends on:** **11a c1-c4 merged to main** (Adapter Protocol + supervisor + 3 cross-cutting schemas + Gmail pack at `packs/adapters/messaging/gmail/`). **11b c1-c4 merged to main** is not a hard dep but is consulted for the sensitivity-floor enforcement pattern. Both confirmed on main at authorship time (2026-05-16, post-recovery).

**Quality bar:** clones `packs/adapters/messaging/gmail/` pack shape with Reminders-specific substitutions. Mirrors the auth → cursor → ingest → emit flow but with EventKit's bidirectional read/write semantics, 30s outbound loop, dedupe-by-remind_id on inbound, and never-sync filtering applied at both adapter boundaries.

**Stop condition:** Apple Reminders adapter pack (`packs/adapters/reminders/apple-eventkit/`) implements the `Adapter` Protocol; reads from iCloud's EventKit via the local `EventKit.framework` Python bridge (or a stubbed Phase A surrogate); writes household-tagged reminders back to iCloud on a 30s outbound loop; filters never-sync lists/tags/ownership at BOTH boundaries; emits `reminder.list_item.synced` (new event type, v1) per dedupe-resolved sync cycle. One new event type registers at v1 in a new `adminme/events/schemas/reminders.py` module. `config/channels.yaml` extended with a Reminders entry alongside Gmail + Plaid. ≥20 new tests across the four commits (highest per-c-band count in the cohort per split memo §2). `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~85-100 minutes wall clock, 4 commits. ~$80-120 API spend per split memo §2 (highest in cohort due to bidirectional complexity).

---

## §1 Behavioral grounding

You are building the **third L1 standalone adapter** and the first **bidirectional** one. The two prior adapters (Gmail, Plaid) are ingest-only: external → event log. Apple Reminders flows in both directions: external (iCloud) → event log AND event log → external (iCloud). The bidirectional surface is what makes this the highest-complexity c-band cohort per the split memo §2.

**Bidirectional sync model** [BUILD.md APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC]. AdministrateMe's value as the household-task surface depends on reminders appearing in the user's existing iOS/macOS Reminders app on iCloud. Two flows:

- **Inbound:** poll EventKit's `EKEventStore.fetchReminders(matching:completion:)` every 30s [BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Polling cadence", `docs/reference/apple-eventkit/fetch.md`]. Filter via never-sync rules. Dedupe via `remind_id` (Apple's `EKReminder.calendarItemIdentifier`). Emit `reminder.list_item.synced` per genuinely-new or modified reminder.

- **Outbound:** subscribe to upstream events (`reminder.dispatched` v1 from `domain.py`, `task.created` v1, `commitment.proposed` v1 once those land in the household member's reminder feed). Write to the household-designated iCloud Reminders list via `EKEventStore.save(_:commit:)` on a 30s loop [BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Outbound write cadence", `docs/reference/apple-eventkit/create.md`]. The 30s cadence batches multiple proposed reminders into one EventKit transaction to minimize iCloud sync churn.

**Never-sync rules (load-bearing)** [BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync rules", VISION §"Privacy by default"]. The household's Reminders app contains lists the user does NOT want AdministrateMe to read or write. The adapter applies a deterministic predicate at BOTH boundaries:

- **List-name substring exclusions:** any list whose name contains `"Work"`, `"Case"`, `"Client"` (case-insensitive). Configurable via `config/channels.yaml`'s `never_sync_list_substrings` (default = the three above).
- **Item tag exclusions:** any reminder whose title or notes contain `#private`. Configurable via `never_sync_tag_substrings` (default = `["#private"]`).
- **Ownership exclusions:** any reminder whose owning household member's role is `"child"` or `"ambient"` (e.g., shared-with-the-family devices, voice-assistant captured). The membership check resolves via the projection `members` slice from prompt 05.

The never-sync filter is applied as a PURE PREDICATE inside the adapter — no projection writes, no skill calls, no LLM. Tests assert that a list named "Work case files" produces zero `reminder.list_item.synced` events even when EventKit returns 200 reminders from it.

**Sensitivity floor: `normal`** [§6 invariant 10]. Reminders sit at household-task granularity — personal but not financial/legal/health. The never-sync filter is the item-level privacy boundary; the sensitivity_floor is the adapter-level policy floor. (Compare to Plaid's `privileged` floor for bank data — Reminders' privacy posture is finer-grained but lower base-rate.) Tests assert `sensitivity_floor` is `"normal"` and cannot be raised above `"normal"` via config (a config setting `"privileged"` is rejected because adapters don't auto-elevate; that's a deliberate operator decision for a future prompt).

**Cursor model: per-list inbound, per-batch outbound** [BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Sync cursor"]. Inbound persists `(list_identifier, last_modified_ts)` per never-sync-passing list; the cursor advances on each poll cycle that returns reminders. Outbound has no cursor — each upstream event triggers a write attempt; the EventKit `calendarItemIdentifier` returned on save is recorded in the emitted `reminder.list_item.synced` event for round-trip traceability. `adapter.cursor_advanced` emission per the established 11a milestone discipline (every 100 reminders OR every 5 minutes per list, whichever first).

**Anti-vision halt** [VISION §"Quiet by default", DRIFT-CONTROL Job 4]. If the inbound poll emits one `reminder.list_item.synced` per reminder per poll cycle (instead of per genuinely-new or modified reminder), stop and report — that's the "stream of everything" anti-pattern. The dedupe-by-`remind_id` check via the `reminders` projection slice (read-only from this adapter; the projection writes are out of scope) is the gate that keeps emission load-bearing instead of noisy. Phase A may stub the projection check; the dedupe predicate's contract must remain "emit on genuinely-new or modified, never on unchanged."

**Mode:** Phase A. No live iCloud sync during tests; recorded EventKit fixtures + mocked `EKEventStore` instance. The bidirectional adapter is the first one to require a `pyobjc-framework-EventKit` (or equivalent shim) dependency under `packs/adapters/reminders/apple-eventkit/`. Tests substitute a fake `EKEventStore` via constructor injection per PM-27.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` ranges. The `apple-eventkit/` reference docs are all small (≤14 KB); full-read each.

1. **`docs/06-split-memo-11.md`** — full read. The split memo defines 11c's scope (Apple Reminders bidirectional, highest per-c-band complexity in the cohort). Read §2 (scope table for 11c), §3 (depends on 11a foundation), §5 (constitutional citations).
2. **`docs/07-deferred-adapter-framework-expansion.md`** — full read. Same Gate 0 disposition as 11b: workspace's expanded framework is deferred; main's simple-sequence 11c (this prompt) is canon.
3. **`adminme/adapters/base.py`** — full read. The `Adapter` Protocol from 11a c1 (now on main post-recovery). `AdapterContext`, `AdapterStatus`, `SensitivityFloor` literal type.
4. **`adminme/events/schemas/adapters.py`** — full read. The 3 cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`). The Reminders adapter emits all three.
5. **`adminme/events/schemas/domain.py`** — targeted read of `ReminderDispatchedV1` (the OUTBOUND proactive event; this adapter consumes it to write to iCloud) and the registry.register pattern. **NOTE:** `reminder.dispatched` is the proactive-pipeline event; `reminder.list_item.synced` (this prompt's new event) is the bidirectional-sync audit event. Distinct.
6. **`packs/adapters/messaging/gmail/pack.yaml`** — full read. Clone template for `packs/adapters/reminders/apple-eventkit/pack.yaml`. Substitutions: `family: messaging` → `family: reminders`; `triggers.kind: external_poll` → `triggers.kind: bidirectional_poll` (or `external_poll` with the 30s sub-cadence noted in the docstring); events emitted include `reminder.list_item.synced` plus the 3 cross-cutting adapter events.
7. **`packs/adapters/messaging/gmail/adapter.py`** — full read. Structural reference. Substitutions: Gmail OAuth2 service-account → EventKit's local-permission-granted `EKEventStore`; inbound-only `users.history.list` → bidirectional `fetchReminders` + `save`; `messaging.received` emit → `reminder.list_item.synced` emit; never-sync filter is a new layer not present in Gmail's pattern.
8. **`packs/adapters/financial/plaid/adapter.py`** — targeted read of the `sensitivity_floor` enforcement path in `start()` (the Gate-4-equivalent runtime check pattern) and the 7-tuple defensive exception catch. The Reminders adapter mirrors both patterns.
9. **`packs/adapters/messaging/gmail/config.schema.json`** — full read. Config-schema template. Substitutions: add `never_sync_list_substrings`, `never_sync_tag_substrings`, `inbound_poll_interval_s` (default 30), `outbound_poll_interval_s` (default 30), `target_list_identifier` (the iCloud list AdministrateMe writes to).
10. **`adminme/daemons/adapter_supervisor.py`** — full read. Confirm the supervisor's pack-discovery mechanism handles `packs/adapters/reminders/apple-eventkit/`. The `<family>/<name>/` pattern from 11a is the contract.
11. **`config/channels.yaml`** — full read. Extend with a Reminders entry alongside Gmail + Plaid.
12. **`ADMINISTRATEME_BUILD.md`** section **"APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC"** — targeted via `grep -n '^### APPLE REMINDERS' ADMINISTRATEME_BUILD.md` first, then `sed -n '<start>,<+150>p'`. Read for: polling cadence + outbound write cadence (both 30s), never-sync rules canonical list, dedupe-by-remind_id pattern, cursor semantics, sensitivity floor `normal`.
13. **`docs/reference/apple-eventkit/_index.md`** — full read. Section index + quickstart.
14. **`docs/reference/apple-eventkit/overview.md`** — full read. EventKit framework overview; reminder vs. event distinction.
15. **`docs/reference/apple-eventkit/access.md`** — full read. Authorization flow (`EKEventStore.requestFullAccessToReminders(completion:)`). Phase A tests stub the granted state.
16. **`docs/reference/apple-eventkit/ekeventstore.md`** — full read. The `EKEventStore` class surface. `fetchReminders(matching:completion:)`, `save(_:commit:)`, `remove(_:commit:)`.
17. **`docs/reference/apple-eventkit/ekreminder.md`** — full read. The `EKReminder` model. `calendarItemIdentifier` (the dedupe key), `calendar` (the list), `title`, `notes`, `dueDateComponents`, `priority`.
18. **`docs/reference/apple-eventkit/fetch.md`** — full read. Reminder-fetch predicates (`predicateForReminders(in:)`, `predicateForIncompleteReminders(...)`). Inbound path uses these.
19. **`docs/reference/apple-eventkit/create.md`** — full read. Creating + saving reminders. Outbound path uses `save(_:commit:)`.
20. **`docs/reference/apple-eventkit/changes.md`** — full read. Change-notification semantics (`EKEventStoreChangedNotification`). Phase A: poll-based fallback; live notification path is OPTIONAL deferred to Phase B.
21. **`docs/SYSTEM_INVARIANTS.md`** — targeted: §1 (event log append-only), §6 invariant 10 (sensitivity floor), §7.3 (no projection direct writes), §8 (no LLM SDKs).
22. **`docs/DECISIONS.md`** — read: D6 (no LLM SDK), D7 (schemas at v1), D8 (correlation_id), D11 (privileged for finance/law/health — Reminders is `normal`), D17 (adapters don't write projections).
23. **`docs/build_log.md`** — read **Prompt 11a entry** + **Prompt 11b entry** end-to-end. Carry-forwards into 11c. Particularly: F-3 pack-internal-test rename, `:8081` supervisor port, milestone-only cursor-emission, stacked-merge anti-pattern fix now in PR #106 sidecar (no manual base-change dance expected on 11c).
24. **`prompts/_active/11b-adapter-plaid.md`** — quality-bar reference. Cite shape only; do not copy content. 11c's quality bar is 11b's quality bar.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad note confirming: (a) the Reminders adapter implements `Adapter` Protocol exactly per `base.py`; (b) `sensitivity_floor = "normal"` is a constant (config-rejected if set to `"privileged"` — adapters don't auto-elevate); (c) never-sync filter applies at BOTH inbound AND outbound boundaries; (d) `reminder.list_item.synced` is one event per genuinely-new-or-modified reminder, NEVER per unchanged poll-cycle entry; (e) cursor lives at `(list_identifier, last_modified_ts)` granularity with milestone-only `adapter.cursor_advanced` emission; (f) F-3 pack-internal-test rename applies to the new pack.

---

## §3 Reconciliation notes (carry-forwards from 11a + 11b merges)

- **F-3 HARD (10c-iii, reinforced by 11a + 11b):** pack-internal test uses unique basename. Pattern: `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_adapter_pack_internal.py`.
- **PM-27 SOFT (graduated to convention):** Reminders adapter's `EKEventStore` instance is a constructor-injected callable with a no-op default. Tests substitute a `_FakeEKEventStore` that returns canned reminders.
- **Stacked-merge anti-pattern fix (NEW — PR #106 on main):** c2/c3/c4 PRs from this prompt's executor cycle will open with `base=main` automatically. No manual base-change dance. If a ghost conflict surfaces on c2 after c1 merges (squash-merge artifact), resolve in one click via "Accept incoming" or "Accept both" in the GitHub web editor.
- **Constitution-sync rule (PR #97):** this PR introduces a new module `adminme/events/schemas/reminders.py` and a new event type `reminder.list_item.synced`. NOT a constitutional doc change — the cascade rule doesn't fire. CI check should pass without an exemption block.
- **Surface drift carry-forward (Gate 0):** workspace pinned `PROMPT_SEQUENCE.md` still shows the expanded 11c "Cat-B adapters" scope. Per operator Option C disposition (carry-forward from 11b), main is canon. Standing decision applies.
- **NEW deferred-watch:** the `reminders` projection slice (which the dedupe-by-remind_id predicate reads from) is partially scaffolded but the writer pipeline is deferred to a future prompt. Phase A: the dedupe predicate is permissive (treats absence-of-record as "new"); production tightens this once the projection's writer lands.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/adapters/base.py` has the full `Adapter` Protocol (post-11a-c1 + 11a-recovery-c2-c4 merges). Verified at authorship time (2026-05-16); confirm at c1 start.

A2. `adminme/events/schemas/adapters.py` registers the 3 cross-cutting schemas at v1. Verified at authorship.

A3. `packs/adapters/messaging/gmail/` directory and its 5 files (pack.yaml, adapter.py, config.schema.json, config.example.yaml, tests/test_gmail_adapter_pack_internal.py) exist on main from 11a c2-c4 recovery (PR #104). **DEPENDENCY:** confirm at c1 start. If missing, halt — 11c cannot proceed without the clone template.

A4. `packs/adapters/financial/plaid/adapter.py` exists on main (PR #105 recovery). Used as the sensitivity_floor enforcement reference; 11c mirrors the `start()` runtime-check pattern. If missing, the reference is the original 11b refactored prompt's §1 spec text.

A5. `adminme/daemons/adapter_supervisor.py` exists (from 11a c1). The supervisor's pack-discovery mechanism handles `packs/adapters/<family>/<name>/` for arbitrary `<family>` including `reminders`. Confirm at c1 start.

A6. No prior `adminme/events/schemas/reminders.py` module exists. Verify with `mcp__github__get_file_contents(path="adminme/events/schemas/reminders.py", ref="main")` returning 404. If it exists already, halt and reconcile — the scope assumption changes. (Note: `reminder.dispatched` v1 lives in `domain.py`, which is correct — `reminders.py` is the NEW module specifically for the bidirectional-sync event family.)

A7. `docs/reference/apple-eventkit/` contains all 8 reference files (`_index.md`, `access.md`, `changes.md`, `create.md`, `ekeventstore.md`, `ekreminder.md`, `fetch.md`, `overview.md`). Verified at authorship.

A8. The `pyobjc-framework-EventKit` package (or equivalent shim under `adminme/lib/eventkit_shim/`) is installable in the Phase A test environment. If not, the Phase A adapter exposes a Protocol-shaped `EKEventStoreLike` and the production wiring lands in the bootstrap-wizard (prompt 16). Verify at c2 start; if absent, use the shim path.

---

## §5 Objective

Ship the Apple Reminders bidirectional adapter pack at `packs/adapters/reminders/apple-eventkit/`, a new `adminme/events/schemas/reminders.py` module registering `reminder.list_item.synced` at v1, an extension to `config/channels.yaml` with a Reminders entry, and ≥20 new tests across the four commits. The pack clones the Gmail adapter's structural pattern from 11a with Reminders-specific substitutions: EventKit-backed bidirectional sync, 30s polling cadences in both directions, never-sync filter applied at both boundaries, dedupe by `remind_id` for inbound emission. Establishes the reminders-adapter family for any future entries (none currently planned).

---

## §6 Out of scope

- **Google Calendar / CalDAV adapters** — prompt 11d.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.
- **The `reminders` projection's writer pipeline** — Reminders adapter EMITS `reminder.list_item.synced` events. The projection that consumes them and materializes the dedupe-key index ships in a future prompt. For 11c, only the event-emit half is in scope (the dedupe predicate reads from the projection's READ-ONLY surface; if the surface is absent on main, the predicate is permissive — see §3).
- **Live iCloud sandbox sync** — tests use recorded EventKit fixtures + mocked `EKEventStore`. Lab-mode integration test against the operator's real iCloud is OPTIONAL (skipped in CI; manual operator-run, deferred to Phase B smoke test in prompt 19).
- **`EKEventStoreChangedNotification` change-stream subscription** — Phase A uses poll-based detection (30s cadence). The live notification path is deferred to Phase B per `docs/reference/apple-eventkit/changes.md`.
- **Bootstrap-wizard EventKit permission flow** — prompt 16 (`16-bootstrap-wizard.md` §"Reminders pairing") wires the OS-level permission grant. Out of scope here; 11c assumes permission is granted at adapter `start()` time. (If not granted, `start()` raises and the supervisor surfaces the failure for operator action.)
- **Outbound for `crm.surfaced` / `custody_brief.composed` / `digest.composed`** — only `reminder.dispatched`, `task.created`, `commitment.proposed` flow to outbound Reminders writes in 11c. The other event types route elsewhere (inbox, SMS, etc.); their adapter is a future-prompt concern.
- **Per-member list assignment heuristic** — for 11c, the household-designated target list is configured statically via `config/channels.yaml`'s `target_list_identifier`. The "auto-pick member's preferred list" heuristic ships in a future personalization prompt.
- **Conflict resolution for the "I edited it in iCloud while AdministrateMe was writing" race** — Phase A uses last-write-wins semantics. The conflict-detection + merge UI lands in the Reminders projection's UI surfaces (prompt 14b/14c).

---

## §7 Deliverables

### New event type (Pydantic model in NEW module `adminme/events/schemas/reminders.py`)

| Type | Class | Required fields | Notes |
|---|---|---|---|
| `reminder.list_item.synced` | `ReminderListItemSyncedV1` | `member_id: str`, `remind_id: str` (Apple's `calendarItemIdentifier`), `list_identifier: str`, `list_name: str`, `title: str` (max 500), `notes: str \| None` (max 2000), `due_at: datetime \| None`, `priority: Literal[0,1,5,9]` (0=none,1=high,5=medium,9=low per EventKit), `completed: bool`, `last_modified_at: datetime`, `direction: Literal["inbound","outbound"]`, `sync_cycle_id: str` | One event per genuinely-new or modified reminder. NEVER per unchanged poll-cycle entry. `direction="inbound"` for iCloud→event-log; `direction="outbound"` for event-log→iCloud writes (round-trip traceability). |

`model_config = {"extra": "forbid"}`. `registry.register("reminder.list_item.synced", 1, ReminderListItemSyncedV1)` at module end.

### Reminders adapter pack at `packs/adapters/reminders/apple-eventkit/`

- **`pack.yaml`** — manifest. `kind: adapter`, `family: reminders`, `triggers: {kind: external_poll, schedule: "*/1 * * * *"}` (1-minute cron schedule; the in-process 30s sub-cadence is enforced inside the adapter), `depends_on.event_types: [adapter.error, adapter.authenticated, adapter.cursor_advanced, reminder.list_item.synced, reminder.dispatched, task.created, commitment.proposed]`, `config.schema: config.schema.json`. Documentation-only metadata cites BUILD.md APPLE REMINDERS BIDIRECTIONAL section + `docs/reference/apple-eventkit/_index.md`.

- **`adapter.py`** — implements `Adapter` Protocol. ~320 lines.
  - `__init__(*, ek_event_store_factory=<no-op default>, now_factory=<from-ctx>)` per PM-27.
  - Class attributes: `name = "reminders-apple-eventkit"`, `family = "reminders"`, `version = "1.0.0"`, `sensitivity_floor = "normal"`.
  - `start(ctx)`: assert sensitivity_floor `"normal"` from config (reject if anything other than `"normal"`); instantiate `EKEventStore` via factory; emit `adapter.authenticated` with `token_storage_ref="os://reminders-permission"` (no 1Password ref — OS-level grant); spawn inbound poll task (30s) AND outbound poll task (30s); block on stop event.
  - `_inbound_poll()`: every 30s, call `_fetch_reminders_filtered()`; for each item where dedupe predicate returns "new or modified", emit `reminder.list_item.synced` with `direction="inbound"`; advance cursor on milestone.
  - `_outbound_poll()`: every 30s, drain queued upstream events (`reminder.dispatched` / `task.created` / `commitment.proposed`); for each non-never-sync target, construct an `EKReminder` and call `save(_:commit:)`; emit `reminder.list_item.synced` with `direction="outbound"` and the returned `calendarItemIdentifier`.
  - `_apply_never_sync_filter(reminders, lists)`: pure predicate. Returns the subset that passes ALL THREE rules (list-name substring NOT in never-sync list AND tag substring NOT in title/notes AND owning member's role NOT in `{"child","ambient"}`).
  - `_dedupe(reminder)`: read from the `reminders` projection slice (or use permissive fallback per §3); return `True` if `remind_id` is new OR `last_modified_at` is newer than the projected `last_modified_at`.
  - Defensive error catch on EventKit's exception hierarchy (mirror Plaid's 7-tuple shape; map to `AdapterErrorV1` severity + `error_kind`).
  - `stop()`: cancel both poll tasks; release `EKEventStore`.
  - `status()`: return current `AdapterStatus` snapshot.

- **`config.schema.json`** — JSON schema. Required: `target_list_identifier`, `member_id`. Optional: `never_sync_list_substrings: [str]` (default `["Work","Case","Client"]`), `never_sync_tag_substrings: [str]` (default `["#private"]`), `inbound_poll_interval_s: int` (default 30), `outbound_poll_interval_s: int` (default 30). **`sensitivity_floor` is a constant `"normal"`**, NOT user-settable.

- **`config.example.yaml`** — example with placeholder list identifiers.

- **`tests/test_apple_eventkit_adapter_pack_internal.py`** — pack-load canary (F-3 rename) + handler-direct tests.

### Extension to `config/channels.yaml`

Append a Reminders entry alongside Gmail + Plaid:
```yaml
  - kind: reminders-apple-eventkit
    family: reminders
    sensitivity_floor: normal
    config:
      target_list_identifier: "AdministrateMe Household"
      member_id: member-001
      never_sync_list_substrings: ["Work", "Case", "Client"]
      never_sync_tag_substrings: ["#private"]
      inbound_poll_interval_s: 30
      outbound_poll_interval_s: 30
```

### Tests (total ≥20 across the four commits)

- **Unit (c1) — 5+ schema + never-sync canaries:** `reminder.list_item.synced` registers at v1, accepts required fields, rejects extra keys, `priority` validates `{0,1,5,9}` only, `direction` validates `{"inbound","outbound"}` only. Never-sync filter predicate: list-name substring rejects matching list, tag substring rejects matching item, ownership rejects child/ambient member.
- **Unit (c2) — 4+ outbound write tests:** outbound poll drains `reminder.dispatched` queue and calls `save(_:commit:)`; never-sync filter blocks outbound write to a "Work" list; outbound emits `reminder.list_item.synced` with `direction="outbound"` and returned `calendarItemIdentifier`; `sensitivity_floor` cannot be raised via config.
- **Unit (c3) — 6+ inbound poll + dedupe tests:** inbound poll fetches reminders from a recorded fixture; never-sync filter is applied (200 reminders from "Work case files" → 0 events); dedupe predicate returns "new" on first sight + "modified" on changed `last_modified_at` + "skip" on unchanged; emit `reminder.list_item.synced` with `direction="inbound"`; cursor advances at milestone; the dedupe permissive-fallback path works when the projection is absent.
- **Unit (c4) — 2+ supervisor lifecycle tests:** adapter starts → both poll tasks running → stop cancels both cleanly; `EKEventStore` permission rejection raises with the `AdapterError` shape the supervisor expects.
- **Pack-internal (c4):** 1 pack-load canary (F-3 rename).
- **Integration (c4):** 2 round-trips, `@pytest.mark.skip_in_ci`. (a) supervisor starts → Reminders adapter spawned → inbound fixture poll → `reminder.list_item.synced` in log; (b) upstream `reminder.dispatched` → outbound poll fires → `EKEventStore.save` called → `reminder.list_item.synced` with `direction="outbound"` in log.

Floor: ≥20 new tests across 11c. Overshoot welcome.

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows the format `phase 11c commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands (PR #106 sidecar made this the workflow default).

### Commit 1 — Schema + program + never-sync rules

**Files:** `adminme/events/schemas/reminders.py` (NEW), `tests/unit/events/test_reminders_schemas.py` (NEW), `packs/adapters/reminders/apple-eventkit/pack.yaml` (NEW), `packs/adapters/reminders/apple-eventkit/config.schema.json` (NEW), `packs/adapters/reminders/apple-eventkit/config.example.yaml` (NEW), `packs/adapters/reminders/apple-eventkit/never_sync.py` (NEW; the predicate as a stand-alone module so c2 + c3 can import + test it in isolation), `tests/unit/adapters/test_never_sync_predicate.py` (NEW).

**Files-to-read:** 6 (`adapters.py` for schema template, `gmail/pack.yaml` for manifest template, `gmail/config.schema.json` for config template, `plaid/pack.yaml` for sensitivity_floor declaration shape, BUILD.md APPLE REMINDERS BIDIRECTIONAL section, the split memo §2).

**Verification:**
```
poetry run python -c "from adminme.events.schemas.reminders import ReminderListItemSyncedV1; print('OK')"
poetry run mypy adminme/ packs/adapters/reminders/ 2>&1 | tail -10
poetry run pytest tests/unit/events/test_reminders_schemas.py tests/unit/adapters/test_never_sync_predicate.py -v
git add adminme/events/schemas/reminders.py tests/unit/events/test_reminders_schemas.py packs/adapters/reminders/apple-eventkit/pack.yaml packs/adapters/reminders/apple-eventkit/config.schema.json packs/adapters/reminders/apple-eventkit/config.example.yaml packs/adapters/reminders/apple-eventkit/never_sync.py tests/unit/adapters/test_never_sync_predicate.py
git commit -m "phase 11c commit 1: reminder.list_item.synced schema + never-sync predicate + pack manifest"
```

Expected: mypy clean; ≥5 schema tests + ≥3 never-sync predicate tests pass.

### Commit 2 — Outbound EventKit write path

**Files:** `packs/adapters/reminders/apple-eventkit/adapter.py` (NEW; outbound path only — inbound `_inbound_poll` raises `NotImplementedError`), `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_adapter_pack_internal.py` (NEW, F-3 rename; pack-load canary + outbound tests), `packs/adapters/reminders/apple-eventkit/_eventkit_shim.py` (NEW; Protocol-shaped `EKEventStoreLike` if pyobjc isn't available — see Assumption A8).

**Files-to-read:** 5 (`gmail/adapter.py` for structural template, `base.py` for Protocol, `apple-eventkit/ekeventstore.md`, `apple-eventkit/create.md`, `apple-eventkit/ekreminder.md`).

**Verification:**
```
poetry run mypy packs/adapters/reminders/ 2>&1 | tail -5
poetry run pytest packs/adapters/reminders/apple-eventkit/tests/ -v
git add packs/adapters/reminders/apple-eventkit/adapter.py packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_adapter_pack_internal.py packs/adapters/reminders/apple-eventkit/_eventkit_shim.py
git commit -m "phase 11c commit 2: outbound EventKit write path"
```

Expected: ≥5 tests pass (pack-load canary + 4 outbound tests).

### Commit 3 — Inbound polling + dedupe + never-sync filter

**Files:** Update `packs/adapters/reminders/apple-eventkit/adapter.py` (replace `NotImplementedError` stub with full `_inbound_poll` + dedupe + cursor persistence). Add recorded fixtures at `packs/adapters/reminders/apple-eventkit/tests/fixtures/`. Add inbound tests to the existing test file.

**Files-to-read:** 6 (adapter.py from c2, `apple-eventkit/fetch.md`, `apple-eventkit/changes.md`, `domain.py` for `reminder.dispatched` shape, `plaid/adapter.py` for the milestone-cursor-advance pattern, `never_sync.py` from c1).

**Verification:**
```
poetry run mypy packs/adapters/reminders/ 2>&1 | tail -5
poetry run pytest packs/adapters/reminders/apple-eventkit/tests/ -v
git add packs/adapters/reminders/apple-eventkit/adapter.py packs/adapters/reminders/apple-eventkit/tests/
git commit -m "phase 11c commit 3: inbound polling + dedupe + never-sync filter"
```

Expected: ≥6 additional inbound tests pass.

### Commit 4 — Integration + BUILD_LOG

**Files:** Update `packs/adapters/reminders/apple-eventkit/adapter.py` with both-poll-tasks-running supervision in `start()`. Add 2 supervisor lifecycle tests. Add `tests/integration/test_adapter_reminders_supervisor.py` (2 round-trips, `@pytest.mark.skip_in_ci`). Append to `docs/build_log.md`.

**Files-to-read:** 3 (existing `tests/integration/test_adapter_supervisor_gmail.py` from 11a c4 for template, build_log tail for append point, BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Polling cadence" for the supervised-poll contract).

**Build_log Evidence template:** Append AFTER 11b entry. Header: `### Prompt 11c — Apple Reminders bidirectional adapter`. Mirror 11a/11b Evidence-section density. Cite: 1 new event type at v1 (`reminder.list_item.synced`); the bidirectional sync model (first adapter in the build with both inbound + outbound); never-sync filter at both boundaries; dedupe-by-remind_id pattern; the 30s polling cadences; carry-forwards for 11d (clone-template paths now include Gmail + Plaid + Reminders); soft-watch entries for the deferred `reminders` projection writer pipeline and the deferred `EKEventStoreChangedNotification` live-stream path.

**Verification:**
```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_adapter_reminders_supervisor.py packs/adapters/reminders/apple-eventkit/
git commit -m "phase 11c commit 4: integration test + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+20; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 11c: Apple Reminders bidirectional adapter`. Body: top line "Third sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`. Ships the first bidirectional adapter (inbound iCloud→event-log + outbound event-log→iCloud), cloning 11a's Gmail-adapter-pack pattern with Reminders-specific substitutions: EventKit-backed bidirectional sync, 30s polling cadences in both directions, never-sync filter at both boundaries, dedupe by `remind_id`. `sensitivity_floor: normal`." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` (per PR #106 sidecar; the workflow handles this automatically). The pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

If a ghost conflict surfaces on c2/c3/c4 after a prior c-band merges (squash-merge artifact pattern), resolve in one click via the GitHub web editor: "Accept incoming" for files where the prior squash already contains the content, "Accept both" for additive cases (e.g., `build_log.md`). No recovery PR needed.

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥20 new tests passing; ruff clean on `adminme/` / `packs/` / `tests/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks (enforced by `verify_invariants.sh`):

- [§7.3, D17]: zero projection writes from the Reminders adapter (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' packs/adapters/reminders/` returns 0 matches). The adapter READS from the `reminders` projection for dedupe; that's read-only and permitted.
- [§8.2, D6]: no LLM SDK import anywhere under `packs/adapters/reminders/`.
- [§6 invariant 10]: `sensitivity_floor` is `"normal"` and cannot be raised via config. Test asserts; config-schema enforces.
- [D7]: `reminder.list_item.synced` registers at v1; `registry.latest_version("reminder.list_item.synced")` returns 1.
- [D8]: `correlation_id` set on entry. `AdapterContext.correlation_id_factory` provides it for adapter-originated events.
- [Causation]: every emitted `reminder.list_item.synced` event with `direction="outbound"` carries `causation_id` set to the triggering upstream event id (the `reminder.dispatched` / `task.created` / `commitment.proposed` that triggered the write). Inbound emissions have `causation_id=None` (no upstream event triggered them — iCloud is the source).
- F-3 (10c-iii): pack-internal pack-load test uses unique basename (`test_apple_eventkit_adapter_pack_internal.py`).
- **VISION §"Privacy by default":** the never-sync predicate test suite is itemized — at least one test per rule (list-name substring, item tag, child/ambient ownership). If any rule's test is missing, the build fails.
- **VISION §"Quiet by default":** the dedupe-skip-unchanged test asserts that a poll cycle returning 200 reminders that all match prior state produces 0 events. If this test passes 1+ event, the build fails.

---

## §10 Stop

Apple Reminders bidirectional adapter operational with iCloud (mocked in tests; real-iCloud integration optional for lab-mode). One new event type at v1 in a new `reminders.py` schema module. Normal sensitivity floor enforced. Bidirectional sync (inbound + outbound) working on 30s cadences. Never-sync filter applied at both boundaries. Dedupe-by-`remind_id` keeps emission load-bearing. Ready for prompt 11d (Google Calendar + CalDAV adapters — the last sub-prompt in the prompt-11 cohort; per Option C disposition, this remains the simple-sequence 11d).
