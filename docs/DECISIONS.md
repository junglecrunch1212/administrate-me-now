# AdministrateMe decisions log

_This file is read by Claude Code (or its successor) alongside `SYSTEM_INVARIANTS.md` before every subsequent prompt. Decisions here are as binding as invariants. When you read this, treat each CONFIRMED decision as a constraint. If a prompt asks you to violate one, stop and report. If you believe a decision is wrong, propose a change in a new entry; do not silently work around it._

Format: each decision is numbered, states the resolution of a specific ambiguity, cites where the ambiguity came from, and records the date decided. Decisions are append-only — superseding decisions create new numbered entries that reference the old one.

---

## D1 — Standing orders registration path

**Decided:** 2026-04-23. **Status:** CONFIRMED. **Resolves:** SYSTEM_INVARIANTS.md §16 item 1; architecture-summary.md §11 item 1.

Proactive pipelines register with OpenClaw via workspace-prose `AGENTS.md` entries paired with `openclaw cron add` invocations. There is no programmatic plugin-hook registration path — the OpenClaw docs do not expose one.

**Corollary:** AdministrateMe ships a `bootstrap/openclaw/programs/` directory containing one markdown file per standing order (scope / triggers / approval gate / escalation / execution steps / what-NOT-to-do). Bootstrap §8 concatenates these into `~/Chief/AGENTS.md` and runs `openclaw cron add` per a sidecar cron spec (`bootstrap/openclaw/cron.yaml`). The markdown files are version-controlled; the concatenated AGENTS.md is a generated artifact, not hand-edited.

## D2 — Console fan-out architecture

**Decided:** 2026-04-23. **Status:** REJECTS proposed §16 item 2 as stated; replaces with stricter formulation.

The Node console has **exactly one** event subscription to the Python event bus — via the HTTP bridge's SSE endpoint on Core `:3333/api/core/events/stream`. Internal console fan-out (chat stream proxy, reward toast dispatch, degraded-mode banner, general event notifications) is a separate concern handled by a `ClientFanOut` class that multiplexes that one upstream subscription to many browser tabs.

Two classes, two responsibilities: `UpstreamBusSubscriber` (one) and `ClientFanOut` (many). "One unified Bus" is explicitly rejected — that shape collapses into a god-object.

## D3 — View mode contract

**Decided:** 2026-04-23. **Status:** CONFIRMED with sharpening. **Resolves:** SYSTEM_INVARIANTS.md §16 item 3; architecture-summary.md §11 item 3.

Each profile pack exports a default React functional component accepting `{member, persona, data, api}` (typed via `@adminme/profile-types`) and renders the full surface for that member. "View mode" (`carousel` / `compressed` / `child`) is descriptive vocabulary for the three reference profile implementations — it is **not** an enum the shell dispatches on, and **not** a parameter any backend endpoint reads. Future profile authors can invent new visual treatments without touching the shell.

## D4 — CRM ownership

**Decided:** 2026-04-23. **Status:** REJECTS proposed §16 item 4 as stated; replaces with the correct decomposition. **Resolves:** architecture-summary.md §11 item 4.

The CRM (parties + identifiers + memberships + relationships + interactions + commitments + artifacts) is a **shared L3 projection concern, not a product concern**. Any Python product may read these projections directly via its local `Session` connection. Mutations flow through events, never through cross-product HTTP calls — e.g. when Core's `closeness_scoring` pipeline recomputes a tier, it emits `party.tier.recomputed`, not `POST /api/capture/parties/<id>`.

The Capture product owns the **human-facing CRM surfaces** (`/api/capture/parties`, the CRM view, triage queue). It does **not** own CRM data. Core, Comms, and Automation are peers of Capture for CRM reads.

This is the mental model: **products own surfaces, projections own data, events move state.**

## D5 — xlsx behavior during observation mode

**Decided:** 2026-04-23. **Status:** CONFIRMED with addition. **Resolves:** SYSTEM_INVARIANTS.md §16 item 5; architecture-summary.md §11 item 5.

The forward xlsx projector runs **unconditionally** regardless of observation mode, because the workbook is a purely local artifact and observation mode gates external side effects only.

**Addition:** Every forward write emits `xlsx.regenerated` with `observation_mode_active: true|false` on the payload. The observation-review pane queries these events so the tenant can see which workbook writes happened during the observation period.

## D6 — openclaw-memory-bridge event emission

**Decided:** 2026-04-23. **Status:** CONFIRMED with specificity. **Resolves:** SYSTEM_INVARIANTS.md §16 item 6; architecture-summary.md §11 item 6.

The `openclaw-memory-bridge` plugin emits events into AdministrateMe via `POST http://127.0.0.1:3334/api/comms/ingest/conversation-turn` (Comms product). The endpoint is loopback-only, authenticated with a shared secret written to `~/.adminme/config/plugin-secrets.yaml.enc` during bootstrap §8. The plugin does **not** open a SQLCipher connection and does **not** hold the AdministrateMe master key.

Rationale: keeps key material inside AdministrateMe; keeps the plugin thin; gives one well-defined ingest endpoint for future similar plugins.

## D7 — schema_version semantics and upcasters

**Decided:** 2026-04-23. **Status:** CONFIRMED with teeth. **Resolves:** SYSTEM_INVARIANTS.md §16 item 7.

`schema_version` is a **monotonically increasing integer per `event_type`** (not semver, not a string). Upcasters are pure functions named `upcast_v{N}_to_v{N+1}(payload: dict) -> dict` registered in `adminme/lib/event_types/<namespace>/<type>/upcasters.py`. Reading an old event composes upcasters in order up to the current version. Downcasting is explicitly not supported — the system only moves forward.

## D8 — correlation_id and causation_id discipline

**Decided:** 2026-04-23. **Status:** CONFIRMED with two additions. **Resolves:** SYSTEM_INVARIANTS.md §16 item 8.

Base rule: the originating adapter or L5 surface sets `correlation_id`; it is preserved unchanged through every derived event. `causation_id` is set to the event_id of the immediate parent. Neither is ever overwritten downstream.

**Addition 1:** Every adapter and every L5 surface endpoint generates a `correlation_id` on entry if the inbound request does not carry one. The originating event always has a correlation_id.

**Addition 2:** Static analysis rule — every `EventStore.append()` call site must pass both `correlation_id` and `causation_id` as explicit keyword arguments. Passing `None` is allowed when genuinely unknown; defaulting them via function signature is not. A lint rule (ruff custom check or grep-based test) enforces this.

---

## Additional decisions not from SYSTEM_INVARIANTS.md §16

### D9 — Hearth relationship

**Decided:** 2026-04-23. **Status:** CONFIRMED.

For v1, "Hearth" refers to a **conceptual base layer**, not a separate installable package. The `hearth.event_types` entry point is vocabulary for "core event types that live in the AdministrateMe repo under `adminme/lib/event_types/hearth/`," distinguishing them from AdministrateMe-specific types under `adminme/lib/event_types/adminme/`. There is no `pip install hearth-core`; there is no separate repo.

If Hearth is ever extracted to a separate package (post-v1), that extraction happens via a dedicated phase prompt with a migration plan; it does not happen opportunistically.

### D10 — Platform version lock

**Decided:** 2026-04-23. **Status:** CONFIRMED.

Platform versions for v1: **Python 3.11**, **Node 22 LTS**. These match what prompt 00's sandbox-downgrade established. Version upgrades happen via a dedicated phase prompt with a migration plan; they do not happen opportunistically, and no prompt adds a dependency that requires a newer minimum version without the upgrade prompt having run first.

**Superseded by:** D30 for future releases. Existing deployments remain on
their reviewed runtime until D30's separate cutover gate is authorized.

### D11 — Skill pack directory shape

**Decided:** 2026-04-23. **Status:** CONFIRMED.

Every skill pack has exactly this shape:

```
~/.adminme/packs/skills/<namespace>/<name>/
├── SKILL.md                   # OpenClaw-format frontmatter + prompt body
├── input.schema.json          # AdministrateMe convention; wrapper enforces
├── output.schema.json         # AdministrateMe convention; wrapper enforces
├── handler.py                 # optional: Python post-processing
├── tests/
│   ├── fixtures/*.json        # input/output pairs for replay tests
│   └── test_skill.py          # contract test: schema compliance + one happy path per fixture
```

No variations. A skill pack that adds or omits files outside this contract fails `adminme pack install` at the validate-manifest stage.

### D12 — Documentation mirror versioning

**Decided:** 2026-04-23. **Status:** CONFIRMED (mechanism below).

`docs/reference/` contents are version-pinned via `docs/reference/_manifest.yaml`, which records the git commit SHA of every upstream source mirrored by prompt 00.5. When a referenced upstream doc changes in a way that affects an AdministrateMe invariant, a dedicated phase prompt updates the mirror AND the affected invariants together.

Without this pin, "per OpenClaw docs" becomes "per whatever version of the OpenClaw docs was in the mirror the day the code was written" — unacceptable for a 10-year system.

### D13 — SQLCipher binding

**Decided:** 2026-04-23. **Updated:** 2026-08-03. **Status:** SUPERSEDED BY CURRENT CROSS-PLATFORM WHEEL. **Resolves:** divergence between prompt 02 / prompt 03 spec text (`pysqlcipher3`) and what actually works.

The SQLCipher binding is **`sqlcipher3==0.6.2`**, not `pysqlcipher3`. Import name remains `sqlcipher3`. The earlier `sqlcipher3-binary` decision was valid for the original Linux sandbox, but version 0.6.0 publishes no Apple wheel. The current `sqlcipher3` release publishes locked CPython 3.11 wheels for Apple silicon, Linux, and Windows and requires no separate SQLCipher Homebrew formula for this deployment.

Every current implementation must use the exact locked `sqlcipher3` distribution in `pyproject.toml` and `import sqlcipher3` in Python. Historical prompt text remains provenance, not an instruction to reintroduce an unavailable platform artifact.

### D14 — SQLCipher async and native-thread ownership model

**Decided:** 2026-04-23. **Updated:** 2026-08-26. **Status:** CURRENT; FORMER DEFAULT-EXECUTOR PRESCRIPTION SUPERSEDED. **Resolves:** seam left open by architecture-summary.md §3 ("In-process event bus. Asyncio queues with durable per-subscriber offsets") and BUILD.md §L2 not specifying the sync-vs-async shape or native-thread ownership of SQLCipher connections.

The public EventLog and ProjectionRunner APIs remain async over the synchronous
`sqlcipher3` DB-API driver. A persistent native connection, however, is created,
migrated, queried, written, checkpointed, and closed only on its owning
single-worker executor. `asyncio.to_thread` is not an ownership boundary: its
default executor may run successive calls on different threads. That earlier
pattern was observed to crash a production-scale encrypted replay inside the
native SQLCipher cursor.

EventLog owns one dedicated worker for its connection. ProjectionRunner owns
one serialized dedicated worker for all of its persistent projection
connections. Compatibility query facades buffer rows and cursor metadata; they
never return a native connection or cursor. Slow workbook rendering uses
private encrypted, query-only projection snapshots on a separate bounded
worker after the persistent owner has checkpointed and copied them, so neither
the raw persistent handles nor the rendering workload escape onto the runtime
event loop. Connection creation and every failure/close path use the same owner
worker, and shutdown settles already submitted native work before terminating
the executor.

This **SUPERSEDES** only the former `asyncio.to_thread` and "reject persistent
workers" prescription. It **PRESERVES** the async API, SQLCipher binding,
append-only event contract, deterministic projections, and absence of a second
database dependency or process-level data service.

### D15 — Instance-path resolution discipline (formalization)

**Decided:** 2026-04-23. **Status:** CONFIRMED. **Resolves:** citation bug in prompt 02. Formalizes SYSTEM_INVARIANTS.md §15 as a numbered decision so later prompts can cite `[D15]` alongside `[§15]`.

Restates §15 in decision form:

- No module under `adminme/`, `bootstrap/`, `profiles/`, `personas/`, or `integrations/` hardcodes a literal instance-directory path (e.g. `~/.adminme/`, `.adminme/`, `~/adminme-lab-data/`) as a runtime string literal. Docstrings explaining the conceptual layout are exempt — only string literals used at runtime count.
- All instance-directory paths resolve through `adminme.lib.instance_config.InstanceConfig`, constructed at service-start time.
- Tests pass an isolated tmp path to `InstanceConfig`; production code resolves through the real config; the bootstrap wizard populates a fresh instance directory. Three callers, one contract.
- The grep-based canary at `tests/unit/test_no_hardcoded_instance_path.py` enforces this at CI time (currently stubbed; implementation lands in the prompt that builds the full `InstanceConfig` behavior).

Prompt 02 cited `[§15/D15]` prospectively; D15 is now the decision that citation points to. All future prompts cite `[D15]` alongside `[§15]` when discussing instance-path discipline.

### D16 — Event log MVP schema vs BUILD.md §L2 full schema

**Decided:** 2026-04-23. **Status:** CONFIRMED. **Resolves:** gap between prompt 03's 9-column MVP schema and ADMINISTRATEME_BUILD.md §L2's 15-column full schema.

Prompt 03 shipped an MVP `events` schema with these columns: `event_id`, `event_at_ms`, `tenant_id`, `owner_scope`, `type`, `version`, `correlation_id`, `source`, `payload`. BUILD.md §L2 specifies these additional columns: `occurred_at`, `recorded_at`, `source_adapter`, `source_account_id`, `visibility_scope`, `sensitivity`, `causation_id`, `raw_ref`, `actor_identity`. This is not a contradiction — the seam was intentional. Prompt 03's job was storage + retrieval; prompt 04's job is typed envelope + payload validation; the richer columns surface naturally once the envelope exists.

Prompt 04 migrates the schema to the full BUILD.md §L2 shape via a new migration file (`0002_full_envelope.sql`). The migration is additive — all new columns are NOT NULL with DEFAULT where BUILD.md allows, NULL-able otherwise — and the existing triggers continue to enforce append-only. No events recorded by prompt 03 are invalidated; the migration back-fills them with defaults (prompt-03 test fixtures are hermetic tmp-dirs, so this only matters at all if someone kept a dev event log across phases).

Additionally, `event_id` is **TEXT** (prompt 03's 17-char Crockford base32 string), not BLOB as SYSTEM_INVARIANTS.md §1 invariant 5 hinted ("ULID, 16 bytes"). Rationale: TEXT is sortable, debug-friendly, SQLite-idiomatic, and interoperable with the JSON API surface. SYSTEM_INVARIANTS.md §1 invariant 5 is corrected by this decision — the invariant's intent was "opaque time-sortable identifier," and a Crockford-encoded ULID in TEXT is equivalent. Future readers of §1 should read it as "event_id — time-sortable identifier, TEXT column, 17 chars of `ev_` + Crockford base32."

The BUILD.md §L2 `append()` signature uses keyword-only `correlation_id` and `causation_id` per D8 addition 2. Prompt 03's `append(event: dict)` accepts them as optional dict fields instead. Prompt 04 shifts the signature to `append(envelope: EventEnvelope, *, correlation_id: str | None = None, causation_id: str | None = None)` as part of the typed-envelope rollout. This is a breaking change for the two prompt-03 demo files; prompt 04 updates them.

### D17 — Personal knowledge ingestion is L1-bridge-shaped, not L5-product-shaped

**Decided:** 2026-04-29. **Status:** CONFIRMED. **Resolves:** drift between BUILD.md §L5-continued / architecture-summary.md §9 framing of Capture as a quick-capture / triage / voice-ingest input pipeline and the binding intent that personal knowledge is ingested per-member from each member's own knowledge tools (Apple Notes by default; Voice Notes; Obsidian opt-in; connector packs for other systems).

Personal knowledge captures live in each family member's own tooling on their own device. AdministrateMe ingests this knowledge via **member bridges**: a Mac Mini per Apple-using family member, on the household tailnet, signed into that member's iCloud account, running an `adminme-bridge` daemon with knowledge-source adapters (Apple Notes, Voice Notes, optionally Obsidian). Bridges emit owner-scoped `note.*` and `voice_note.*` events to the central CoS Mac Mini's `:3337 bridge` ingest endpoint, where Tailscale identity binds the owner_scope.

The Capture product (`:3335`) is a **read surface** over the resulting knowledge layer + the CRM projections — not an input pipeline. Quick-capture prefix routing, triage queues, and central voice-note ingest are explicitly retired.

**Partially superseded by D26:** Capture remains read-only and quick-capture /
central voice ingest remain retired. D26 reintroduces Triage as a separate,
member-owned adjudication surface for derived proposals, not as Capture ingest.

Tasks, commitments, recurrences, and relationships flow through the existing reactive pipelines (`commitment_extraction`, `recurrence_extraction`, `relationship_summarization`), with `note.*` and `voice_note.*` added to their subscription lists. There is no "polling" of Capture; pipelines react to events as they always have.

Connector packs for non-Apple knowledge sources (Notion, Logseq, Roam, etc.) install on bridges as `kind: adapter, subkind: knowledge-source` packs.

**Kid-bridge variant.** Bridges assigned to child members run a restricted adapter set: Apple Notes + Voice Notes only, no Obsidian. Cross-member knowledge graph derivation that touches kid-owned events is sandboxed per the Conception-C amendment §2.5. This is the physical-layer reinforcement of [§6.12].

**Corollary 1:** D4 ("products own surfaces, projections own data, events move state") still applies. Knowledge events are just one more event family flowing into the projection layer.

**Corollary 2:** [§6.12] (identity-first privacy) is **strengthened** by this decision. Each member's private knowledge is physically segregated on their own bridge's iCloud account; the central system never holds member iCloud key material.

### D18 — Lists are a 13th first-class projection; `reminder.*` retired

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** drift between BUILD.md §3.5 / arch-summary §4 row 3.5 modeling Apple Reminders state as `task.*` events feeding the `tasks` projection, and the binding intent that household lists are first-class entities distinct from tasks with round-trip mirror semantics, sharing models, and write-back asymmetries that don't fit `tasks`'s shape.

Lists are a 13th first-class projection (`lists`) sitting alongside `member_knowledge` as the 12th. List items are NOT tasks: a grocery line, a household honey-do entry, a checklist item in a shared Notes document — these are list items in `lists`, not rows in `tasks`. The `tasks` projection retains its full ADHD-neuroprosthetic field set (energy, effort, micro_script, waiting_on, goal_ref, life_event); the `lists` projection has the structurally simpler list-item shape (body, status, position, notes, sharing model).

Promotion from list item to task is supported via the `list_item.promoted_to_task@v1` event. Promotion does NOT modify the source list item — the item retains its upstream-mirror state; the resulting task is a new row in `tasks` carrying `source_list_item_id` provenance. `list_items.promoted_task_id` cross-links back to the materialized task.

External surfaces are SSOT mirrors: Apple Reminders, Google Tasks, Apple Notes-checklists are upstream sources of truth; AdministrateMe's `lists` projection mirrors. Bidirectional where the upstream supports it. Notes-checklists supports `toggle_completion` + `add_item` only — no remove, no in-place text edit, no reorder (per UT-19). Apple Reminders + Google Tasks support full CRUD.

The `tasks` projection's `reminder.*` subscription is retired (UT-22 closure). `reminder.*` events are no longer emitted; the canonical event family for list state is `list.*` / `list_item.*`. The `tasks` projection's new subscription includes `list_item.promoted_to_task` to materialize promoted tasks.

**Corollary 1:** D4 ("products own surfaces, projections own data, events move state") still applies. Lists are just one more projection family.

**Corollary 2:** [§6.12] (identity-first privacy) interacts with the
iCloud-shared-list mechanism. Every member bridge first submits a private,
exact member/account/container observation. Apple-native sharing is not an
AdministrateMe grant and cannot self-promote that fact. Only a reviewed
content-free `shared_container_ids` grant sealed into the active bridge
authorization may restamp the container `shared:household`; the `lists`
projection then collapses the shared provider/container identity while
retaining every member/account observation for provenance and last-observer
deletion. Private observations remain route-separated.

### D19 — Adapter taxonomy is by epistemic role; runtime is orthogonal; capabilities are a list

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** taxonomy drift introduced by Conception-C amendment of 2026-04-29 — that amendment introduced a third runtime variant for L1 adapters (bridge-side knowledge adapters) alongside the two pre-existing variants (OpenClaw-plugin channel adapters; standalone-Python data adapters), but left the L1 inventory organized by runtime substrate when the more decision-relevant axis is epistemic role.

Adapter classification is by **epistemic role**, not by runtime substrate. Five categories:

- **Cat-A — Communication.** People-talking-to-people surfaces. Bidirectional required (inbound/outbound symmetry). Examples: Gmail, BlueBubbles/iMessage, Telegram, Discord.
- **Cat-B — External-State-Mirror.** Round-trip mirrors of state the principal directly maintains in an external system. AdministrateMe both reads from and writes to the external system. Examples: Apple Reminders, Google Tasks, Apple Calendar, Google Calendar, Apple Contacts, Google Contacts.
- **Cat-C — Inbound-Only Data.** External data the principal does not directly maintain. Read-only from AdministrateMe's side. Examples: Plaid (financial), Stelo / Apple Health (future Phase B+).
- **Cat-D — Personal-Knowledge.** Per-member knowledge captures from each member's own preferred tool. Owner-scope is always `private:<member_id>`. Examples: Apple Notes (prose half), Apple Voice Memos, Obsidian.
- **Cat-E — Outbound-Action.** System acts in the world via the adapter; outbound-primary with minimal-confirmation inbound. Examples: Twilio outbound voice/SMS (per D21), Home Assistant service calls (per D24), future brokerage trading and postal mail.

Runtime substrate (central / bridge / separately reviewed service-owned
implementation) is an **orthogonal secondary axis**. A category does not
dictate a runtime: Cat-B has central Google variants and member-bridge Apple
variants. The current human-member Apple Calendar, Reminders, and Contacts
schemas are bridge-only; a future central Apple service source requires a
distinct owner/schema/permission review and cannot reuse a member credential
boundary. Cat-D is bridge-only by current convention but architecturally
permits a separately reviewed central implementation. Multi-capability
adapters declare each capability as its own seam: Apple Notes-checklists
declares Cat-D (prose) + Cat-B (checklists); Home Assistant declares Cat-C
(state-read) + Cat-E (service-call).

**Corollary:** the framework's five abstract base classes (per BUILD.md §ADAPTER FRAMEWORK §3.2) — `CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter`, `OutboundActionAdapter` — match one-to-one with the categories. A capability inherits from exactly one base class; multi-capability adapters compose multiple capability classes within a single pack manifest.

### D20 — Adapter packs ship in three developer-mode tiers

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** the question of how third-party-authored adapter packs are admitted to a running instance, given that Cat-E adapters can take outbound action and Cat-C/Cat-D adapters can ingest privileged data.

Adapter packs install through the existing pack-registry infrastructure (per BUILD.md §PACK REGISTRY); the five-category taxonomy refines what manifests declare but does not replace the install lifecycle. Three layers of trust:

- **Bundled adapters.** Default-on. Code-reviewed by AdministrateMe maintainers and shipped in the repo under `packs/adapters/<name>/`. Examples: every adapter in the v1 build plan.
- **Verified third-party adapters.** Available with `developer_mode_enabled: true` in instance config. Pack manifest carries a signed-manifest signature (verification keys distributed via a future ClawHub-equivalent or by manual operator import). Verified-tier adapters can declare `kind: cat_a`, `cat_b`, `cat_c`, `cat_d`, or `cat_e` and any combination of write-capabilities subject to install-time validation.
- **User-authored adapters.** Available with `developer_mode_enabled: true` AND scaffolding flag set; lands via `adminme adapters scaffold` CLI. Cannot declare `kind: cat_e` without explicit operator confirmation per-adapter at install time (warm-aware default — Cat-E from unverified code is the highest-risk admission).

Developer mode is per-instance, not per-tenant. Bootstrap defaults to OFF; operator opts in via `adminme config set developer_mode_enabled true` and a one-time consent log entry in the event log.

### D21 — Twilio is Cat-E (outbound fallback), not Cat-A

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** SMS placement ambiguity — SMS-via-Twilio was previously informally bundled with Cat-A messaging adapters, but the practical use-case is outbound-only fallback when iMessage delivery fails, plus optional outbound voice calls.

Twilio is Cat-E (Outbound-Action). Inbound SMS is **deferred to v2** — the v1 use-case is "send a critical message via SMS when iMessage isn't available," which is outbound-only and matches the Cat-E shape. The Twilio adapter declares `write_capabilities: [send_sms, place_voice_call]` and `observation_mode_required: true`. iMessage-as-SMS-fallback routing logic lives at the messaging-router layer; the Twilio adapter is the bottom-of-stack Cat-E delivery seam.

If a future v2 build adds inbound SMS handling (alphanumeric short codes, two-way SMS chat with non-iMessage parties), it lands as a separate Cat-A capability on the same Twilio pack manifest, declared as a second capability per [D19].

**Partially superseded by D28:** two-way Twilio SMS/MMS and WhatsApp are now
admitted Cat-A capabilities; proactive SMS/voice remains Cat-E.

### D22 — Apple Calendar is Cat-B; current human-member source is bridge-only

**Decided:** 2026-04-29-B. **Clarified:** 2026-08-27 under CO-019.
**Status:** CONFIRMED AS MODERNIZED. **Resolves:** v1 calendar gap for
Apple-using households without collapsing a member Apple identity into the
Central Host or the planned CoS service identity.

Apple Calendar is a Cat-B (External-State-Mirror) adapter. The current
human-member implementation runs only inside each selected member's separately
supervised bridge against that member's Apple ID. Its manifest and schema
require `deployment_target: bridge`, an exact content-free provider-account
reference, personal workspace, exact container allowlist, permission scope,
binding provenance, and ownership attestation. The one central adapter loader
validates but never imports or constructs it.

Transport enrollment alone grants no Calendar authority. A member explicitly
enables the sealed pack, the bridge emits `adapter.authenticated@v2` bound to
the enrollment, full configuration digest, provider account, and exact EventKit
scopes, and the central owner separately attests that retained event before
ordinary Calendar facts or commands are admitted. Native calendar sharing
remains private until an exact reviewed shared-container grant promotes it.

The former human-member **central variant is narrowly SUPERSEDED**. The planned
CoS Apple service identity remains a channel identity with no account,
permission, provider binding, or source authority. Any future service-owned
central Calendar implementation requires a distinct reviewed adapter/schema
and provider gate. Google Calendar remains central; CalDAV remains outside v1
per [D25].

### D23 — Apple Contacts (bridge per-member) + Google Contacts (central)

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** the CRM-spine-empty-on-day-1 gap — without a contacts adapter in v1, the `parties` projection's `identifiers` table starts empty and the CRM has no real material until messaging adapters slowly populate it from observed senders.

Two contacts adapters in v1 scope:

- **Apple Contacts.** Bridge runtime, per-member. Reads from each member's iCloud Contacts via Contacts.framework on the bridge Mac Mini. Cat-B with `write_capabilities: [update_party_identifier]` (sparse — most contacts work is reading; writes back when the operator merges/de-merges identifiers in the AdministrateMe Parties view).
- **Google Contacts.** Central runtime. Reads from the assistant's Google Workspace Contacts via the People API. Cat-B; `write_capabilities` parallel.

Both adapters feed `parties.identifiers`. Deduplication on `(external_id_kind, external_contact_id)`. Sharing model is implicit (Apple Contacts is per-Apple-ID; Google Contacts is per-Workspace).

**Lands at new prompt 11e.**

### D24 — Home Assistant is Cat-C+E reference, multi-capability, full bidirectional

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** UT-23 (Cat-E reference implementation) and the `/api/automation/ha/*` orphan in BUILD.md §L5 (router list referenced HA without a real backing adapter in Phase A scope).

Home Assistant is the v1 reference implementation for Cat-E (Outbound-Action). HA is a multi-capability adapter declaring two capabilities:

- **Cat-C state-read seam.** Subscribes to HA's WebSocket `subscribe_events` channel for state changes (or polls the REST `/api/states` endpoint as fallback). Emits `ha.state_changed@v1` events. `observation_mode_required: false` (reading state is internal logic).
- **Cat-E service-call seam.** Consumes `ha.service_call_requested@v1` events from the event log; calls HA's REST `POST /api/services/<domain>/<service>`; emits `action.executed@v1` (success) or `action.failed@v1` (error). `observation_mode_required: true` (service calls are external side effects). When `observation_mode = active`, emits `observation.suppressed@v1` with the full would-have-sent payload and does NOT call HA's REST endpoint.

Both seams ship in Phase A. **Lands at new prompt 11g.**

The `/api/automation/ha/state` and `/api/automation/ha/services` routers in BUILD.md §L5 (Automation product) gain real backing: `ha/state` serves cached state populated by the Cat-C seam; `ha/services` accepts service-call POSTs that emit `ha.service_call_requested` events into the event log, where the Cat-E seam consumes them with full observation-mode integration per [§6.20].

**Corollary:** HA is the canonical PR-γ-2 test case for `OutboundActionAdapter`'s observation-mode integration. Cat-A messaging-outbound and Cat-E service-call both pass through `adminme/lib/observation.py` per [§6.14] / [§6.20].

### D25 — L1 adapter inventory cleanup (Stelo, Lob, Privacy.com, CalDAV, Drive, iOS Shortcuts)

**Decided:** 2026-04-29-B. **Status:** CONFIRMED. **Resolves:** UT-24 — eleven specific drifts in the L1 inventory between BUILD.md §L1 / arch-summary §1 and the binding architectural intent post-D17 + D18 + D19.

Phase A v1 inventory cleanup:

- **Stelo / Dexcom CGM.** Cat-C (health-telemetry). **NOT in Phase A scope.** Architectural placeholder noted in memo §1.3; v2 community pack post-Phase-A.
- **Lob (postal mail).** Cat-E. **DEFERRED to v2 community pack.** Removed from Phase A L1 inventory.
- **Privacy.com (virtual cards).** Cat-E. **DEFERRED to v2 community pack.** Removed from Phase A L1 inventory.
- **CalDAV (separate adapter).** **REMOVED from v1 per [D22].** Apple Calendar covers iCloud calendars; Google Calendar covers Google calendars; a CalDAV-server adapter is unnecessary for v1 Apple-using or Google-using households. v2 community pack if a non-iCloud-non-Google CalDAV server is needed.
- **Google Drive.** **DEFERRED to v2.** The Drive-as-document-source role is covered for Apple-using households by Apple Notes (prose) per [D17]; Google-Workspace-only households can defer document ingestion to a later prompt without blocking the v1 CRM/Triage/lists/calendars/finance core.
- **iOS Shortcuts webhooks.** **REMOVED.** Functionally retired by [D17] — knowledge-source ingestion via member bridges supersedes the Shortcuts webhook pattern. The webhook adapter pattern remains available in the framework (any Cat-A/B/C/D/E adapter can run a webhook receiver as its inbound source) but iOS Shortcuts as a named v1 adapter is retired.
- **iCloud (as separate adapter).** **CLARIFIED.** iCloud is not its own adapter; it is accessed via the Apple Reminders / Apple Notes / iMessage / Apple Calendar / Apple Contacts adapters' use of the relevant Apple ID. The `iCloud` row formerly in L1 inventory is removed.
- **Apple Reminders.** **MODERNIZED under CO-019.** The current human-member
  source is member-bridge-only. The former central human-member variant is
  narrowly **SUPERSEDED**; a future service-owned source requires a distinct
  contract.
- **Apple Calendar.** **NEW v1 per [D22], MODERNIZED under CO-019.** The current
  human-member source is member-bridge-only with separate transport and exact
  permission-attestation gates.
- **Apple Contacts.** **NEW v1 per [D23].** Bridge runtime per-member.
- **Google Contacts.** **NEW v1 per [D23].** Central runtime.

**Net inventory delta:** −5 (CalDAV, Drive, Shortcuts, iCloud, Lob/Privacy.com as Phase A entries) +3 (Apple Calendar, Apple Contacts, Google Contacts) = −2 v1 line items + 1 multi-capability cross-cat extension (Apple Notes gains checklist B-half).

**Modifies prompts 11b (member-bridge Apple Calendar and Reminders + Google
Tasks central + CalDAV removed), 11c-ii (Apple Notes-checklists B-half), and
adds new prompts 11e (Contacts) + 11f (lists projection) + 11g (Home
Assistant).**

### D26 — Triage is the human gate for derived household candidates

**Decided:** 2026-08-06. **Status:** CONFIRMED. **Supersedes:** D17 only where
it retired every triage queue, and the older Inbox surface semantics in BUILD,
PATTERNS, the console reference, and Gate 7.

The stable adult surface previously labeled **Inbox** is now **Triage**. Triage
contains only extracted or inferred candidates that require a member to
confirm, enrich, reroute, or dismiss them. It does not mix already-official
tasks or other operational work into the review queue. `Today` and
`Scoreboard` remain the surfaces for official open work.

Source observations remain facts and never wait in Triage merely because they
arrived: an email received, provider calendar event observed, reminder synced,
or list item read from its authoritative provider is appended and projected as
an observation. A model- or rule-derived candidate that would create or change
household truth, or request an external provider write, must instead emit a
proposal. No extractor may emit the target canonical event directly.

Triage supports candidate targets for tasks, commitments, calendar events,
reminders, lists, list items, and proposed CRM identifier links. Confirmation uses a closed typed
materializer. Local task/commitment targets emit their canonical events only
after confirmation. External targets require an explicit member, provider,
provider account, and destination calendar/list route; confirmation emits a
governed `external_sync.write_requested` command, and the calendar/list
projection changes only after the provider write is observed back as fact.
Allowlist, domain governance, rate limit, final privacy/content policy,
observation mode, and terminal auditing remain mandatory.

`triage.item.proposed`, `.enriched`, `.confirmed`, and `.dismissed` form the
generic lifecycle. Existing `commitment.proposed` and
`household.action.proposed` events are projected into the same queue during
migration. A dedicated encrypted `triage` projection is the fourteenth
registered read model; the original thirteen domain-truth projection
capabilities remain intact. Triage is derived and rebuildable, never a source
of truth.

Canonical paths and bindings use `triage` (`/member/triage`,
`/api/core/v1/triage`, `/adminme-triage`, and
`administrateme.dashboard.triage`). The old `inbox` paths and OpenClaw names
may remain as explicitly deprecated compatibility aliases for pinned clients,
but they do not appear in navigation or new documentation. This rename does
not alter external-provider Inbox folders, email inbox terminology, or the
legacy internal task workflow status named `inbox`.

### D27 — Today is a deterministic Daily Brief; routines are ordered occurrences

**Decided:** 2026-08-06. **Status:** CONFIRMED. **Supersedes:** the flat
`{stream, summary, activeIdx, energy, streak, whatNow}` Today-stream contract
in BUILD, the task-only profile examples, and prompt 14b's former Node-console
rendering instructions. It does not supersede any event-authority, scope,
privacy, governance, observation, or OpenClaw boundary.

Today is a read-time composition over authorized projections. Its default Day
Brief groups the member's local-day calendar, executable Tasks and
Commitments, provider List Items, ordered Routine occurrences, actionable
Interactions, and pending Triage questions without copying them into a new
store. The one-item Carousel is a presentation of that same typed composition.
`What Now?` deterministically selects an event in progress or beginning within
30 minutes before falling back to the explained work ranking. No model chooses
the order or implies an outcome.

Carousel `Later` changes browser position only. Task/Commitment completion,
provider List Item completion, calendar attendance/missed outcomes, and
Routine step outcomes use separate typed, idempotent commands. Elapsed
calendar time is not attendance evidence. A linked routine step is not a Task
completion and never completes its linked Task implicitly.

A Routine is a distinct ordered execution aggregate housed in the existing
`recurrences` projection because it shares recurrence scheduling, not because
it is interchangeable with a Recurrence template. `routine.defined@v1`
records an owner-scoped member, IANA timezone, RFC 5545 rule, local time
window, and contiguous ordered steps. `routine.step_outcome_recorded@v1`
records an explicit `completed` or `skipped` outcome for one step of one dated
occurrence. The projection derives the current step and closed status; it does
not emit events. Steps must be handled in order. This adds no fifteenth
projection and does not collapse Tasks, Recurrences, calendar events, or
scheduler jobs.

The member portal and pinned OpenClaw dashboard consume the same
`deterministic-daily-brief-v1` contract. Process acceptance must prove local
time, scope filtering, typed action routing, optimistic concurrency,
idempotent retries, ordered Routine progression, replay, and a model-free
path before Today behavior is considered complete.

### D28 — Comms is a deterministic, member-isolated communications cockpit

**Decided:** 2026-08-06. **Status:** CONFIRMED. **Supersedes:** the eight-surface
count in D26/Gate 7 and D21 only where it deferred all inbound Twilio messaging.
It preserves D21's Cat-E classification for proactive SMS/voice actions.

Comms is the ninth stable adult member surface, immediately after Triage. It is
the working-memory layer over person-to-person email and messaging—not a
second assistant chat, a generic OpenClaw transcript viewer, or a source of
household truth. The default Focus view, account/workspace/channel filters,
Waiting, Snoozed, Quieted, All, thread reader, inline reply, provider-state
actions, capability status, and links to extracted Triage candidates operate on
the existing `interactions` projection.

The combined inbox is an authorized union at query time. Every fact retains
`member_id`, provider, provider account, `personal|work` workspace, native
thread/message identifiers, participants, scope, sensitivity, provider labels,
and raw-event provenance. It is never copied into a cross-account mailbox.
Principals cannot act while viewing as another member; privileged content
remains owner-only; children and ambient/device roles have no Comms route.
Personal and work accounts may be combined visually only after each account
passes the same member authorization independently.

Focus is `deterministic-comms-cockpit-v1`. Fixed rules derive Focus, Needs
Reply, Waiting, Snoozed, Quieted, Archive, scores, and plain-language reasons
from direction, unread/important state, explicit member controls, typed
classification, response urgency, and recency. An LLM cannot choose ordering,
archive, send, or create an official item. NLP through the pinned OpenClaw
`llm-task` seam may emit typed classifications or extraction proposals; those
are auditable inputs to the fixed rules. Calendar events, Tasks, Commitments,
reminders, lists/list items, and CRM links extracted from communications still
become official only through D26 Triage.

Snooze and quiet are AdministrateMe-owned, event-sourced focus state and do not
pretend to change a provider mailbox. Reply/read/archive operations are typed
`external_sync.write_requested` commands. The UI exposes an operation only
when that exact configured account advertises it. Every external mutation
retains allowlist → domain governance → rate limit ordering, explicit member
approval, independent OpenClaw approval, final content/privacy guard,
observation handling, optimistic concurrency, and requested/completed/failed
audit evidence. A provider response or later provider observation—not a UI
click—is the terminal delivery/state fact.

OpenClaw owns member-to-CoS channel conversations and model sessions;
AdministrateMe owns the normalized communication facts and domain policy. A
human-to-human provider message does not enter an OpenClaw model session merely
because OpenClaw can transport that channel. When member DMs do reach OpenClaw,
true person isolation uses one OpenClaw agent per member, with a private
workspace/state/auth/session store and deterministic account/peer binding.
Within one member, `per-account-channel-peer` sessions separate channel
accounts. A second personal/work agent is justified only when model, tool,
workspace, or professional-policy boundaries differ; an extra agent is not a
substitute for AdministrateMe member/account/workspace authorization. Official
OpenClaw routing references reviewed for this decision:
<https://docs.openclaw.ai/concepts/multi-agent>,
<https://docs.openclaw.ai/channels/channel-routing>, and
<https://docs.openclaw.ai/gateway/config-channels>.

Provider capability ceilings are evidence-backed and never universalized:

- Gmail/Google Workspace uses Gmail messages, threads, history, labels,
  `messages.modify`, and `messages.send`. Push uses Pub/Sub `watch`, must be
  renewed, and still needs periodic history fallback because notifications can
  be delayed or dropped. Read-only OAuth is the default; reply/read/archive
  require fresh modify/send consent. Sources:
  <https://developers.google.com/workspace/gmail/api/reference/rest>,
  <https://developers.google.com/workspace/gmail/api/guides/push>, and
  <https://developers.google.com/workspace/gmail/api/guides/sync>.
- Outlook/Microsoft 365 uses Microsoft Graph message/folder APIs, per-folder
  delta, change/lifecycle notifications, reply/forward/draft/send, read state,
  flags, and importance. Shared/delegated mailbox subscriptions require the
  provider's application-permission path rather than pretending delegated
  `Mail.Read.Shared` is sufficient. Sources:
  <https://learn.microsoft.com/en-us/graph/outlook-mail-concept-overview>,
  <https://learn.microsoft.com/en-us/graph/api/message-delta>, and
  <https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview>.
- Apple EventKit is not a Messages-history API. Apple's public Messages and
  MessageUI frameworks support extensions/compose UI, not arbitrary personal
  history ingestion. iMessage/SMS therefore requires a reviewed, explicitly
  trusted per-member local bridge such as BlueBubbles, with Full Disk Access
  and automation implications disclosed. It is never installed or labeled as
  EventKit. Sources: <https://developer.apple.com/documentation/messages>,
  <https://developer.apple.com/documentation/messageui>, and
  <https://github.com/BlueBubblesApp/bluebubbles-server>.
- A Twilio number used for two-way SMS/MMS or WhatsApp declares a Cat-A
  inbound/reply capability; proactive SMS and voice remain a separate Cat-E
  capability on the same pack. Delivery/read status is provider evidence.
  WhatsApp free-form replies are constrained to the provider's 24-hour
  customer-service window; approved templates apply outside it. Sources:
  <https://www.twilio.com/docs/messaging/api/message-resource>,
  <https://www.twilio.com/docs/usage/webhooks/messaging-webhooks>, and
  <https://www.twilio.com/docs/whatsapp/api>.

Missing Outlook, BlueBubbles, Twilio SMS, and Twilio WhatsApp packs remain
truthfully unconfigured/disabled provider edges; their absence cannot remove
the required Comms product function or invite fabricated live-provider claims.
Release acceptance proves navigation/server authorization, account isolation,
body/provenance projection, deterministic grouping/ranking, local focus-state
round trip, stale-write refusal, provider capability denial, observation-mode
suppression, replay, pinned OpenClaw binding, responsive keyboard behavior, and
zero browser/runtime errors.

### D29 — Reminders is a deterministic provider mirror and contextual remembering cockpit

**Decided:** 2026-08-06. **Status:** CONFIRMED. **Supersedes:** the nine-surface
count introduced by D28. **Preserves:** D6, D17, D18, D26, and every external
write/privacy/observation invariant.

Reminders is the tenth stable adult member surface, immediately after Comms.
It is a working-memory prosthetic over member-bound Apple Reminders and Google
Tasks—not a second Task model, a calendar, an LLM-ranked queue, or an
AdministrateMe-owned source of provider truth. The combined view is an
authorized query-time union; every list and item retains provider, member,
account, list, native identifier, revision, scope, sensitivity, and immutable
event provenance.

`deterministic-reminders-cockpit-v1` derives Now, Scheduled, Places, Anytime,
and Completed with fixed code from completion, explicit provider priority,
dates, alarms, recurrence, and member-authored cue matches. Sort order and
plain-language reasons are stable and inspectable. An LLM may extract a typed
proposal through the pinned OpenClaw `llm-task` seam; it cannot decide that a
cue matched, infer a member's location/presence/energy, rank or hide an item,
write a provider, or materialize a Task, Commitment, calendar event,
recurrence, list, or scheduler job. Extracted provider-bound work still passes
through D26 Triage.

Context awareness uses typed `context.signal.observed` facts carrying member
scope, source, observation time, expiry, state/value/unit, accuracy where
applicable, and provenance. A `reminder.cue.configured` rule compares one fact
with one fixed operator. Location, Home Assistant presence/device/network,
calendar transitions, weather thresholds, and explicit member capacity may
activate a lens or notification eligibility only while fresh; they do not
change provider truth. Provider-native EventKit geofences remain provider
alarms rather than duplicated AdministrateMe location rules.

Capability parity may never be invented. Public Apple EventKit supports
calendar/list identity, title, notes, URL, priority, completion, date
components, alarms including structured-location enter/leave proximity, and
recurrence. It does not expose Reminders.app-only tags, sections, subtasks,
templates, assignment, or smart-list semantics. Google Tasks supports task
lists, title, notes, date-only `due`, completion, nesting, opaque ordering,
move, links, and assignment provenance; the API discards due time and exposes
no priority, alarm, recurrence, or geofence. Assigned/recurrent tasks may have
provider-enforced write restrictions. Sources:
<https://developer.apple.com/documentation/eventkit>,
<https://developer.apple.com/documentation/eventkit/ekreminder>,
<https://developer.apple.com/documentation/eventkit/ekstructuredlocation>,
<https://developers.google.com/workspace/tasks/reference/rest/v1/tasks>, and
<https://developers.google.com/workspace/tasks/reference/rest/v1/tasklists>.

Every mutation exposes only an exact configured account capability and passes
allowlist → governance → rate limit, explicit member intent, independent
OpenClaw approval, final content/privacy guard, observation, idempotency,
optimistic concurrency, and requested/completed/failed audit reconciliation.
A UI acceptance is not provider success. Live EventKit/Google propagation is
separate release evidence; absent credentials or grants render read-only or
not configured without weakening the Reminders product contract.

### D30 — Python 3.14 and Node 24 are the target; runtime cutover is explicit

**Decided:** 2026-08-07. **Status:** CONFIRMED. **Supersedes:** D10 as the
future architecture. **Governing ADR:**
`docs/refactor/ADR-0006-RUNTIME-BASELINE-AND-REVIEWED-CUTOVER.md`.

Future central and member-bridge releases target Python `>=3.14,<3.15` and
Node `>=24.15,<25`; Homebrew 6, Poetry 2.4.1, and npm 11 are the matching
toolchain. The pinned OpenClaw version/commit and SQLCipher boundary do not
move as part of this decision.

This decision does not authorize replacement of a running 3.11/22 instance.
The new runtimes are installed side by side, candidate environments are
release-local, and a version-changing upgrade fails before service replacement
without the explicit runtime-migration flag after clean install, replay,
backup/restore, browser, OpenClaw, Doctor, and rollback evidence. A parser
fallback for an older release pointer is migration compatibility, not support
for 3.11 as the future application runtime.

---

## How to use this file

- **Claude Code / future AI agents:** read this file before every prompt alongside `SYSTEM_INVARIANTS.md`. Treat CONFIRMED decisions as constraints. Do not silently violate them. If a new prompt requires revisiting a decision, the prompt itself must say so explicitly and add a superseding entry here.
- **Operator (human):** when a new ambiguity surfaces mid-build, add a numbered decision here before the prompt that depends on it runs. Decisions are cheap; undoing wrong decisions embedded in code is expensive.
- **Superseding a decision:** add a new numbered entry with `**Supersedes:** D<N>` at the top and explain the change. Do not edit historical entries except to add a `**Superseded by:** D<N>` note at the bottom.
