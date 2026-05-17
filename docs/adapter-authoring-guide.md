# Adapter authoring guide

This guide is the developer-facing seam for the AdministrateMe adapter
framework. It documents the manifest extensions defined in prompt 11-α,
the six install-time checks enforced in prompt 11-β, and the
operator-facing CLI (`adminme adapters list / validate / scaffold`)
shipped in prompt 11-γ. Read this guide once end-to-end before
authoring a pack; afterwards, treat sections 5 and 6 as the canonical
reference for each manifest field and each validator rule.

The framework's organizing principle: **packs are inspectable
contracts, not free-form plugins.** Every pack ships a `pack.yaml`
manifest that declares its capabilities and runtime placement; the
validator AST-walks the adapter source against the manifest and
refuses packs whose code drifts from their declared contract. The
goal is not bureaucratic — it is to make pack failures
**install-time, named, and fixable**, not opaque runtime crashes
hidden behind the supervisor's logs.

---

## 1. Introduction — the five-category taxonomy

Every adapter pack belongs to exactly one (or, with multi-capability
manifests, a small set) of five epistemic-role categories per
`docs/05-architecture-amendment-personal-data-layer.md` §2.1:

- **Cat-A — Communication.** Bidirectional messaging adapters that
  both ingest inbound messages and emit outbound replies (Gmail, SMS,
  Slack). Inherits `CommunicationAdapter`. Runtime is `central` or
  `plugin`.
- **Cat-B — External state mirror.** Bidirectional write-capable
  adapters that mirror local state into a remote system and accept
  writes back (Apple Reminders, Google Tasks, calendar). Inherits
  `ExternalStateMirrorAdapter`. Declares a `write_capabilities` list
  on the manifest naming each write operation the adapter handles.
- **Cat-C — Inbound data.** Read-only ingest adapters that pull data
  from a remote system without writing back (Plaid, brokerage
  statements, fitness trackers). Inherits `InboundDataAdapter`. The
  manifest's `write_capabilities` MUST be empty for Cat-C.
- **Cat-D — Personal knowledge.** Bridge-bound adapters that ingest
  data scoped to a single owner's Tailscale-identified node (Apple
  Notes, photos, local files). Inherits `PersonalKnowledgeAdapter`.
  Runtime is hard-pinned to `bridge` per
  `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`
  §2.2.
- **Cat-E — Outbound action.** Adapters that execute side-effecting
  actions in remote systems on behalf of the household (calendar
  invites, third-party API submissions, IoT control). Inherits
  `OutboundActionAdapter`. Manifest MUST declare
  `observation_mode_required: true` per `[§6.20]`.

The taxonomy is exhaustive: every pack falls into one or more of
these five. Multi-capability packs (e.g. Apple Notes acting as both
Cat-D ingest AND Cat-B checklist-write) declare both kinds on the
manifest's `capabilities` list; see §7.

---

## 2. Quickstart — scaffold a Cat-C pack end-to-end

The fastest path from "I want to add a pack" to "the supervisor
accepts it":

```bash
# 1. Enable developer mode (env var + opt-in flag per [D20]).
export ADMINME_DEVELOPER_MODE=1

# 2. Scaffold a Cat-C central skeleton named "weather".
poetry run adminme adapters scaffold Cat-C central \
    --allow-scaffold --yes \
    --name weather --output ./packs/adapters/data/weather

# 3. Fill in the TODO bodies in adapter.py — the connect() method,
#    the start() loop, the on_inbound_event() handler — using the
#    pack-specific HTTP client / SDK / file format.

# 4. Validate the pack against the six install-time checks.
poetry run adminme adapters validate ./packs/adapters/data/weather

# 5. If validation reports zero errors, the supervisor will accept
#    the pack when added to bootstrap/pack_install_order.yaml.
```

The scaffold step emits a `pack.yaml`, an `adapter.py` skeleton
inheriting from the matching ABC, a `config.schema.json` placeholder,
and a `tests/test_<name>_adapter_pack_internal.py` file. The
generated pack is **validator-clean on first run** — `adminme adapters
validate` against the freshly scaffolded directory exits 0 with no
findings. Your edits to `adapter.py` MUST preserve the ABC inheritance
and the observation-mode wrapping; the validator re-checks both on
every install.

---

## 3. Choosing a category

Pick the category by answering, in order:

1. **Does the pack read AND write a remote system?**
   - If the remote system is a messaging surface (mail, SMS, chat) →
     **Cat-A**.
   - If the remote system has structured state you mirror locally
     (tasks, calendar entries, lists) → **Cat-B**. You will declare
     each write operation in `write_capabilities`.
   - If the writes go through a single-shot "send action" (calendar
     invite, third-party API call, IoT command) without back-mirroring
     → **Cat-E**.

2. **Is the pack read-only?**
   - If the data is global to the household (financial, fitness,
     weather) → **Cat-C**.
   - If the data is bound to a single Tailscale-identified node
     (Apple Notes, local photos, a member's `~/Documents/`) →
     **Cat-D**.

3. **Is the pack multi-modal?** A pack can declare more than one
   capability — e.g. Apple Notes-with-checklists is **Cat-D** for
   note ingest AND **Cat-B** for checklist write per memo 05 §2.2. See
   §7 for the canonical worked example.

When in doubt, scaffold the most restrictive category that fits (Cat-C
read-only first; promote to Cat-B if write surface is needed) — the
validator refuses overreach but accepts narrower declarations.

---

## 4. Choosing a runtime

The manifest's `runtime` field places the adapter in one of four
deployment surfaces:

- **`central`** — the adapter runs in the household's central
  AdministrateMe process. Default for Cat-A / Cat-B / Cat-C / Cat-E
  packs that talk to externally-hosted services (Gmail, Plaid,
  Google Tasks). The supervisor handles credentials, retries, and
  failover.
- **`bridge`** — the adapter runs in a per-member bridge process
  bound to a Tailscale-identified node. Hard-pinned for **Cat-D**
  (memo 04 §2.2) because the data being ingested is scoped to a
  single device's local filesystem or local APIs (Apple Notes via
  EventKit, local photos via Photos.framework). The validator
  refuses Cat-D with `runtime: central`.
- **`dual`** — the adapter declares two deployment shapes (central
  AND bridge) for cases like a calendar pack that runs centrally for
  Google Calendar but on a bridge for Apple Calendar via EventKit.
  Memo 05 §2.5 covers the dual shape.
- **`plugin`** — the adapter runs as a separate process loaded by
  AdministrateMe at startup. Reserved for packs that vendor
  conflicting dependencies (e.g. a pack needing a different `httpx`
  version than the rest of the codebase).

Cat-A `messaging`-family packs are restricted to `central` or
`plugin`; the validator refuses `bridge` because messaging surfaces
are household-shared by design.

---

## 5. The manifest extensions

The `pack.yaml` manifest's framework extensions (atop the legacy `pack:
{id, version, family}` block from pre-framework packs) are parsed by
`adminme.lib.pack_registry.parser.parse_adapter_manifest` into an
`AdapterManifest` Pydantic model. Each field is enforced by one of the
six validator checks documented in §6.

### 5.1 `capabilities`

A list of capability blocks. Each block declares one `kind` (Cat-A
through Cat-E), one `runtime`, and one or more `event_families`. A
single-capability manifest declares one block; a multi-capability
manifest (§7) declares two or more.

```yaml
capabilities:
  - kind: Cat-A
    runtime: central
    event_families: [messaging.received, messaging.sent]
```

**Rule:** every event family listed here MUST be registered in the
event-schema registry (`adminme.lib.event_schemas`). Unknown families
fail validation with a top-3 prefix-overlap suggestion list so typos
are easy to fix [D7].

### 5.2 `write_capabilities`

A list of write-operation identifiers for Cat-B and Cat-E adapters.
Each entry MUST correspond to an `async def _handle_<operation>`
method on the adapter class, OR the adapter MUST implement
`async def _apply_write_impl(self, operation)` dispatching on
`operation.kind`.

```yaml
write_capabilities:
  - toggle_completion
  - reorder_list_item
  - rename_list
```

**Rule:** Cat-A, Cat-C, and Cat-D packs declaring non-empty
`write_capabilities` fail validation with a pack-level error per
`[UT-27]` — these categories have no write surface by design.

### 5.3 `sensitivity_default`

The default sensitivity floor for events emitted by this pack. One of
`normal` / `sensitive` / `privileged`. Per-event overrides can RAISE
the floor (a `privileged` event in a `normal`-default pack stays
`privileged`) but never lower it; the floor is enforced at
`AdapterManifest.resolve_effective_sensitivity` in 11-α.

```yaml
sensitivity_default: privileged
```

**Worked example:** the Plaid pack declares `sensitivity_default:
privileged` because every account transaction is financial PII; the
Gmail pack declares `sensitivity_default: normal` because most mail is
non-PII (individual messages may raise their own floor).

### 5.4 `observation_mode_required`

A boolean. When `true`, the supervisor refuses to instantiate the
adapter unless the system-wide observation mode is configured. Cat-E
packs MUST declare `true` per `[§6.20]`; other categories MAY declare
`true` if their write surface has irrevocable consequences (e.g. a
Cat-A pack that sends emails — Gmail declares `true`).

```yaml
observation_mode_required: true
```

### 5.5 `owner_scope_overridability_at_install`

A boolean. When `false`, the supervisor refuses to install this pack
with an owner_scope override at install-time — the pack is bound to
the manifest's declared scope. **MUST be `false` for Cat-D** per
memo 04 §2.2 (the Tailscale-identity-binds-owner-scope property — a
bridge pack ingesting one member's Apple Notes cannot be re-pointed
to a different member without reinstalling).

```yaml
owner_scope_overridability_at_install: false
```

### 5.6 `sharing_model_discriminator`

One of `owner_member_id` / `household_id` / `room_id`. Tells the
projection layer how to scope events emitted by this pack. Most Cat-B
packs declare `owner_member_id` (each task belongs to one member);
shared-resource packs (a household calendar) declare `household_id`.

```yaml
sharing_model_discriminator: owner_member_id
```

### 5.7 `dedup_strategy`

How the supervisor reconciles duplicate events from this pack (e.g. a
mail thread polled twice across a restart). One of `external_id`,
`content_hash`, or `none`. Default: `external_id`.

```yaml
dedup_strategy: external_id
```

---

## 6. The six validator rules

The 11-β validator runs six per-check modules in
`adminme/lib/pack_registry/validator/` against every pack at install
time. The aggregator does NOT short-circuit — every check runs, every
finding is preserved on the returned `ValidationResult`. Each check
yields findings tagged with one of three severities: `error`
(blocks install), `warning` (logged, install proceeds), `advisory`
(informational only).

### 6.1 `capability_runtime_coherence`

**Rule.** Cat-D ⇒ `runtime: bridge`. Cat-D ⇒
`owner_scope_overridability_at_install: false`. Cat-E ⇒
`observation_mode_required: true`. Cat-A with `family: messaging` ⇒
`runtime ∈ {central, plugin}`.

**Failure example:**

```yaml
capabilities:
  - kind: Cat-D
    runtime: central       # ← wrong: Cat-D must be bridge
```

**Diagnostic shape:**

```
ERROR capability_runtime_coherence [capabilities[0].runtime]:
  Cat-D capabilities must declare runtime: bridge (memo 04 §2.2).
```

**Fix:** change `runtime: central` to `runtime: bridge` on the
offending capability block. If the pack truly needs central placement,
re-categorize it as Cat-B or Cat-C (whichever fits the data shape).

### 6.2 `event_schema_registration`

**Rule.** Every entry in any `capabilities[].event_families` list MUST
resolve to a registered Pydantic schema in the event-schema registry
(default: on-main `adminme.lib.event_schemas`). Unknown families ⇒
`error` finding with the top-3 prefix-overlap suggestions in the
message.

**Failure example:**

```yaml
capabilities:
  - kind: Cat-A
    runtime: central
    event_families:
      - messaging.recieved   # ← typo: should be ``received``
```

**Diagnostic shape:**

```
ERROR event_schema_registration [capabilities[0].event_families[0]]:
  Unknown event family ``messaging.recieved``. Did you mean:
  messaging.received, messaging.sent, messaging.draft_created?
```

**Fix:** correct the typo, or register the new family in
`adminme/lib/event_schemas/__init__.py` if the family is genuinely
new (see `docs/D7.md` for the registration discipline).

### 6.3 `projection_subscription_compatibility`

**Rule.** For Cat-B and Cat-D capabilities, every declared
`event_families` entry must match at least one Projection class's
`subscribes_to` list. Cat-A / Cat-C / Cat-E capabilities skip this
check (they emit events for downstream pipelines, not for projection
fan-in).

**Forward-compat carve-out:** families with the `member_knowledge.`
or `lists.` prefix yield `advisory_pending_projection` (not error)
when no matching subscription exists — these projections ship in 11d
and 11f respectively per memo 05 §3.4.

**Failure example:**

```yaml
capabilities:
  - kind: Cat-B
    runtime: central
    event_families: [task.created]   # ← no projection subscribes
```

**Diagnostic shape:**

```
ERROR projection_subscription_compatibility [capabilities[0].event_families[0]]:
  No projection subscribes to ``task.created``. Add it to a
  projection's ``subscribes_to`` list before installing this pack.
```

**Fix:** either add the family to an existing projection's
`subscribes_to` (`adminme/projections/<name>/__init__.py`), or
postpone the pack install until the projection ships.

### 6.4 `base_class_conformance`

**Rule.** The top-level adapter class in `<pack_dir>/adapter.py` MUST
inherit from the ABC matching its declared category — Cat-A ⇒
`CommunicationAdapter`, Cat-B ⇒ `ExternalStateMirrorAdapter`, Cat-C ⇒
`InboundDataAdapter`, Cat-D ⇒ `PersonalKnowledgeAdapter`, Cat-E ⇒
`OutboundActionAdapter` per `[PM-34]`. Legacy v1 packs without a
`capabilities:` block AND without ABC inheritance yield
`advisory_pending_retrofit` (NOT error) for Cohort C backward-compat.

**Failure example:**

```python
# pack.yaml declares Cat-B but adapter.py inherits from the wrong ABC.
class TasksAdapter(CommunicationAdapter):   # ← wrong: should be ExternalStateMirrorAdapter
    pass
```

**Diagnostic shape:**

```
ERROR base_class_conformance [pack_dir/adapter.py]:
  Cat-B capability requires inheritance from ExternalStateMirrorAdapter;
  found CommunicationAdapter on class TasksAdapter.
```

**Fix:** change the base class to `ExternalStateMirrorAdapter` (and
import it from `adminme.lib.adapters`), or change the manifest's
`kind:` field to `Cat-A` if the pack truly is a messaging adapter.

### 6.5 `write_capabilities`

**Rule.** For every entry in `write_capabilities`, the adapter class
MUST implement EITHER `async def _handle_<operation>` (one method
per entry) OR `async def _apply_write_impl(self, operation)`
dispatching on `operation.kind` (the precedent from
`ExternalStateMirrorAdapter`). Cat-A/C/D packs with non-empty
`write_capabilities` fail with a pack-level error per `[UT-27]`.

**Failure example:**

```yaml
# Manifest declares the op…
write_capabilities: [toggle_completion]
```
```python
# …but adapter.py implements no handler.
class TasksAdapter(ExternalStateMirrorAdapter):
    pass
```

**Diagnostic shape:**

```
ERROR write_capabilities [write_capabilities[0]]:
  Declared write operation ``toggle_completion`` has no handler.
  Add ``async def _handle_toggle_completion(self, operation)`` or
  implement ``_apply_write_impl`` with a dispatch on operation.kind.
```

**Fix:** add the handler method to the adapter class OR drop the op
from `write_capabilities` if it's not yet implemented.

### 6.6 `observation_mode_wrapping`

**Rule.** The Cat-A `send` method and the Cat-E `execute_action`
method MUST funnel through `adminme.lib.observation.outbound`. The
validator AST-walks the method body for a call to that exact symbol
and accepts four import shapes: direct `from … import outbound`,
module-qualified `observation.outbound(...)`, fully-qualified
`adminme.lib.observation.outbound(...)`, and aliased
`from … import outbound as do_send`. Cat-B / Cat-C / Cat-D capabilities
skip this check (no outbound surface).

**Failure example:**

```python
class GmailAdapter(CommunicationAdapter):
    async def send(self, message):
        # ← wrong: bypasses the observation-mode suppression seam
        return await self._http_post(message)
```

**Diagnostic shape:**

```
ERROR observation_mode_wrapping [adapter.py:send]:
  send() must funnel through adminme.lib.observation.outbound to honor
  observation mode per [§6.20]. Wrap the network call in
  outbound(session=…, action=…, payload=…, action_fn=…).
```

**Fix:** wrap the outbound network call in `outbound(...)` per the
scaffold template's Cat-A example. Observation mode flips
`outbound(...)` to a no-op so test-drives and dry-runs don't actually
send messages.

---

## 7. Multi-capability adapters

A pack can declare more than one capability in `capabilities[]`. The
canonical worked examples:

### 7.1 Apple Notes-with-checklists (Cat-D + Cat-B)

Per memo 05 §2.2: the Apple Notes adapter ingests notes (Cat-D —
read-only personal knowledge bound to a Tailscale node) AND writes
checklist toggles back to the notes (Cat-B — bidirectional state
mirror). The manifest declares both kinds:

```yaml
pack:
  id: adapter:reminders-notes-checklists
  version: 0.1.0
family: reminders
runtime: bridge

capabilities:
  - kind: Cat-D
    runtime: bridge
    event_families: [note.added]
  - kind: Cat-B
    runtime: bridge
    event_families: [list_item.toggled]

write_capabilities:
  - toggle_checklist_item
  - reorder_checklist

sensitivity_default: sensitive
observation_mode_required: true
owner_scope_overridability_at_install: false
sharing_model_discriminator: owner_member_id
```

The validator runs **all six checks** across both capability blocks:
`base_class_conformance` checks for inheritance from the
**broader** category's ABC (here `PersonalKnowledgeAdapter`, since
Cat-D dominates the runtime placement), `write_capabilities` checks
the Cat-B handlers, `event_schema_registration` checks both
families, and so on.

### 7.2 Home Assistant (Cat-C + Cat-E)

Per `[D24]`: Home Assistant exposes both sensor reads (Cat-C —
inbound data) and switch toggles (Cat-E — outbound actions). The
manifest declares both kinds, with `observation_mode_required: true`
honoring the Cat-E rule:

```yaml
capabilities:
  - kind: Cat-C
    runtime: central
    event_families: [iot.sensor_reading]
  - kind: Cat-E
    runtime: central
    event_families: [iot.action_dispatched]

write_capabilities:
  - turn_on_switch
  - turn_off_switch
observation_mode_required: true
```

---

## 8. Scaffolding new packs (developer mode)

The `adminme adapters scaffold` subcommand is the ONLY CLI surface
that creates files. Per `[D20]`, it is gated behind a **three-layer
developer-mode check** — all three layers MUST pass before scaffold
proceeds:

1. **Environment variable.** `ADMINME_DEVELOPER_MODE=1` must be set
   in the process environment. Any other value (including unset, `0`,
   `true`) refuses scaffold with **exit code 3** and the stderr
   message `scaffold refused: developer mode not enabled — set
   ADMINME_DEVELOPER_MODE=1.`
2. **Explicit flag.** `--allow-scaffold` must be passed on the
   command line. The flag is intentionally awkward to type so it
   doesn't become muscle memory; the subcommand refuses without it
   even when `ADMINME_DEVELOPER_MODE=1` is set. Missing the flag
   refuses with **exit code 4**.
3. **Interactive confirmation.** `typer.confirm("Scaffold a new
   adapter pack at <output>?")` defaults to `False` — pressing Enter
   without typing `y` declines and refuses with **exit code 5**.
   The `--yes` flag skips this prompt for automation use ONLY after
   layers 1 and 2 have passed.

Refusal on any layer exits with the distinct code above so scripted
operators can distinguish refusal modes without scraping stderr.
This is **the canonical place** the three-layer gate is enforced —
no other CLI subcommand creates files; no other gate weakens this
discipline (e.g. a `.developer-mode` file on disk is explicitly
rejected per prompt 11-γ §6, because a stale file could enable
scaffolding the developer didn't intend).

The scaffold's output:

- `pack.yaml` — manifest with the right `capabilities` block for the
  chosen category and runtime; placeholder pack name + family
  derived from `--name`.
- `adapter.py` — adapter class inheriting from the matching ABC, with
  TODO bodies and the observation-mode wrapping baked in for Cat-A /
  Cat-E.
- `config.schema.json` — JSON Schema placeholder for the pack's
  install-time configuration.
- `tests/test_<name>_adapter_pack_internal.py` — pack-internal test
  file with the F-3 unique-basename rule (10c-iii precedent).

The generated pack passes all six validator checks on first run —
`adminme adapters validate <output>` exits 0 immediately after
scaffold completes. Subsequent edits to `adapter.py` MUST preserve
the ABC inheritance and the observation-mode wrapping; the validator
re-checks both on every install.

---

## 9. Backward-compat with v1 manifests

Three packs predate the framework: `packs/adapters/messaging/gmail/`,
`packs/adapters/financial/plaid/`, and
`packs/adapters/reminders/apple-eventkit/`. Each carries no
`capabilities:` block and implements the simple `Adapter` Protocol
directly (not the new ABCs). The 11-β validator surfaces this gap as
an **advisory, not an error**:

```
ADVISORY base_class_conformance [adapter.py]:
  advisory_pending_retrofit: legacy v1 pack predates the framework.
  Cohort C will retrofit this pack to inherit from the matching ABC
  and declare a capabilities block.
```

The advisory does NOT block install — the three packs continue to
work through the legacy code path. Cohort C retrofit prompts close
the gap one pack at a time; after each retrofit, the advisory
naturally becomes a pass (the validator stops emitting it because
the inheritance + manifest are now correct).

If you are authoring a NEW pack, **do not follow the v1 pattern** —
scaffold via `adminme adapters scaffold` and inherit from the
matching ABC. The advisory is a carry-forward for already-shipped
packs, not a sanctioned path for new work.

---

## 10. Where to look in the codebase

When the validator surfaces a finding you don't understand, these are
the canonical locations to inspect:

- **The 5 ABCs.** `adminme/lib/adapters/__init__.py` re-exports
  `CommunicationAdapter`, `ExternalStateMirrorAdapter`,
  `InboundDataAdapter`, `PersonalKnowledgeAdapter`, and
  `OutboundActionAdapter`. Each lives in its own per-ABC module
  documenting the abstract methods.
- **The manifest model.** `adminme/lib/pack_registry/models.py`
  defines `AdapterManifest` (Pydantic v2) and `Capability`. Reading
  these dataclasses tells you exactly which fields are required vs
  optional and their default values.
- **The manifest parser.** `adminme/lib/pack_registry/parser.py`
  exposes `parse_adapter_manifest(pack_dir) -> AdapterManifest` and
  raises `ManifestParseError` on malformed input.
- **The validator.**
  `adminme/lib/pack_registry/validator/__init__.py` exposes
  `validate_adapter_pack(pack_dir, *, registry=None, projections_dir=None) -> ValidationResult`.
  The six per-check modules are sibling files:
  `capability_runtime_coherence.py`, `event_schema_registration.py`,
  `projection_subscription_compatibility.py`,
  `base_class_conformance.py`, `write_capabilities_check.py`, and
  `observation_mode_wrapping.py`.
- **The CLI.** `adminme/cli/commands/adapters.py` ships the three
  subcommands. The Typer subcommand group `adapters_app` is wired
  into `adminme/cli/main.py` via `app.add_typer(adapters_app,
  name="adapters")`.
- **The scaffold templates.**
  `adminme/cli/commands/adapters_scaffold/templates/` contains five
  per-category template directories. Each is a flat tree of
  `pack.yaml.j2`, `adapter.py.j2`, `config.schema.json`, and a
  `tests/` subdirectory. Read the relevant template before
  scaffolding to see exactly what the output will look like.
- **The three reference packs.** `packs/adapters/messaging/gmail/`,
  `packs/adapters/financial/plaid/`, and
  `packs/adapters/reminders/apple-eventkit/` are the canonical
  worked examples of pre-framework packs. Cohort C will retrofit
  them onto the new ABCs; until then, treat their pack.yaml shape as
  the legacy v1 pattern — do NOT copy it for new packs.
- **The constitutional documents.**
  `docs/05-architecture-amendment-personal-data-layer.md` is the
  canonical source for the five-category taxonomy and the
  multi-capability shape. `docs/SYSTEM_INVARIANTS.md` §6.20 covers
  the observation-mode rule, §7.3 covers the no-direct-writes rule
  for adapters, and §8 covers the no-LLM-SDK rule. `docs/DECISIONS.md`
  entries D6, D7, D8, D17, D18, D19, D20, and D24 are referenced by
  the validator and the CLI throughout.

Read these top to bottom before authoring a non-trivial pack; the
guide above is the entry point, but the constitutional documents are
the authority.
