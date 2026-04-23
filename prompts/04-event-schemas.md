# Prompt 04: Event schemas + schema registry

**Phase:** BUILD.md PHASE 1 (schema half) and PHASE 2 (schema registry).
**Depends on:** Prompts 01a/01b/01c/02/03/03.5 merged to main. Event log + bus operational. `docs/DECISIONS.md` contains D13-D16.
**Estimated duration:** 2.5–3.5 hours across four batch commits.
**Stop condition:** Envelope migration lands cleanly; registry validates on append; 15 canonical schemas ship; validation integrated into `EventLog.append()`; all prompt-03 tests still pass; ~25 new tests pass; demo produces expected output including one planned validation failure.

---

## Phase + repository + documentation + sandbox discipline

You are in Phase A: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that would require those are marked `@pytest.mark.requires_live_services` and skipped.

Sandbox egress is allowlisted. `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 — expected, move on.

**Session start (required sequence):**

```bash
git checkout main
git pull origin main
git checkout -b phase-04-event-schemas
# (harness may override with claude/<random>; work on whatever branch gets assigned)
```

**Verify all six constitutional reference documents are on main before touching code:**

```bash
ls -la docs/SYSTEM_INVARIANTS.md docs/DECISIONS.md docs/architecture-summary.md \
       docs/openclaw-cheatsheet.md docs/reference/_manifest.yaml \
       adminme/events/log.py adminme/events/bus.py
```

If any file is missing, STOP and report. Prerequisites are incomplete.

---

## Read first (required, in this order)

1. **`docs/DECISIONS.md`** — read in full, but pay particular attention to **D7** (schema_version semantics — monotonic integer per event_type; upcasters are pure functions `upcast_v{N}_to_v{N+1}(payload: dict) -> dict`), **D8** (correlation_id / causation_id discipline — explicit kwargs at every `EventStore.append()` call site), **D13** (sqlcipher3-binary), **D14** (async EventLog via `to_thread` + write lock — do not propose alternatives), **D15** (instance-path discipline), **D16** (MVP schema → full schema migration in this prompt; `event_id` is TEXT; `append()` signature shift).

2. **`docs/SYSTEM_INVARIANTS.md`** §1 (event log sacred — especially invariants 3 (validation rejects bad payloads), 5 (full row schema), 8 (immutable but correctable), 9 (every event_type ships a Pydantic model + schema_version + upcaster on schema change)). Skim §§2-15; they constrain related choices.

3. **`adminme/events/log.py`** — the prompt-03 MVP. You are extending its schema and signature. Read the module docstring and the `append`/`append_batch` signatures carefully before changing them.

4. **`adminme/events/migrations/0001_initial.sql`** — the MVP schema. You are adding `0002_full_envelope.sql` on top.

5. **`adminme/events/registry.py`** and **`adminme/events/schemas/`** — stubs from prompt 02. You are filling them in.

6. **`ADMINISTRATEME_BUILD.md`** — three targeted sections, with `view_range` if your tooling supports it:
   - **§"L2: THE EVENT LOG (SOURCE OF TRUTH)"** — the full 15-column schema. Pay attention to which columns are NOT NULL.
   - **§L2 event taxonomy** — grep `^\|` tables and `event.*` strings to find the canonical type names.
   - **§L4 CONTINUED: THE SKILL RUNNER** — the `skill.call.recorded` event's payload shape.

7. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §5** — the canonical `commitment.proposed` event. Model the corresponding schema on this example.

8. **`docs/architecture-summary.md` §3** (event log invariants).

Do NOT read:
- Projection details (prompts 05-07).
- The L4 pipeline sections in full — you need the event names they emit, not their implementation.
- The console patterns (prompts 14a-c).

---

## Operating context

Every event AdministrateMe emits has a schema. The schema is a Pydantic model that validates the `payload` field. The registry maps `(event_type, schema_version)` to model classes and validates events at append time.

This prompt does four things:

1. **Migrate the event log schema** from prompt 03's 9-column MVP to BUILD.md §L2's 15-column full shape, per D16. New migration file `0002_full_envelope.sql`. Additive columns with NOT NULL + DEFAULT where safe, NULL-able otherwise. Existing triggers preserved.

2. **Define the typed envelope** (`adminme/events/envelope.py`) matching the new 15-column row. Every event read from the log deserializes to an `EventEnvelope`.

3. **Build the schema registry** (`adminme/events/registry.py`) with `(event_type, schema_version)` → `type[BaseModel]` dispatch, upcasters per D7, validation on append.

4. **Ship 15 canonical payload schemas** covering every L1 adapter family, the CRM spine, the domain spine, the skill runner, and observation. Later prompts add their own using the same pattern.

**This prompt does NOT define every schema.** The full catalog is ~60 models and would take too long to do carefully. Later prompts (05 through 14) add their schemas as their sections are built.

---

## Out of scope

- Do NOT define every event schema. Later prompts add their own; each such prompt's Deliverables section names the new schemas.
- Do NOT implement schema upcasters yet. The registry supports them structurally (per D7), but no upcaster is required because every schema below is v1 (except `skill.call.recorded` v2, which ships without a v1 upcaster — see the note below).
- Do NOT emit events in this prompt. You are defining schemas; sending them is later.
- Do NOT touch projections. That's prompt 05.
- Do NOT reopen D13-D16. If a decision feels wrong, flag it in the commit message; do not silently work around it.

---

## Incremental commit discipline — MANDATORY

Four batch commits. Same anti-timeout pattern that worked for 01b, 01c, 02.

**Commit 1 — schema migration + envelope model.**
- Write `adminme/events/migrations/0002_full_envelope.sql` adding the columns from BUILD.md §L2 that MVP lacks: `schema_version` (INTEGER NOT NULL — backfill from the existing `version` column), `occurred_at` (TEXT NOT NULL — backfill from `datetime.fromtimestamp(event_at_ms/1000, UTC).isoformat()`), `recorded_at` (TEXT NOT NULL — same), `source_adapter` (TEXT NOT NULL DEFAULT `'unknown:legacy'`), `source_account_id` (TEXT NOT NULL DEFAULT `'legacy'`), `visibility_scope` (TEXT NOT NULL — backfill equal to `owner_scope`), `sensitivity` (TEXT NOT NULL DEFAULT `'normal'`), `causation_id` (TEXT — NULL allowed), `raw_ref` (TEXT — NULL allowed), `actor_identity` (TEXT — NULL allowed). Add indexes `idx_events_causation` (per SYSTEM_INVARIANTS.md §1 invariant 5's implied index list). Existing `idx_events_owner_scope_time` and `idx_events_type_time` continue to exist; no drops.
- Update `EventLog._migrate()` if needed — if the existing loader already picks up new migration files by directory scan, you may not need to change it; if it only knows migration 0001 explicitly, extend the list.
- Write `adminme/events/envelope.py`: the `EventEnvelope` Pydantic model matching the 15-column row. `event_id: str` (per D16 — TEXT, not BLOB). `owner_scope`, `visibility_scope` as typed strings with a comment pointing to SYSTEM_INVARIANTS.md §1 invariant 6 for the valid prefixes. `sensitivity: Literal["normal", "sensitive", "privileged"]`. `payload: dict` (validation happens in the registry, not here).
- Update `adminme/events/log.py::_row_to_event` to return the richer dict (include the new columns). `EventLog.get`, `read_since`, `get_by_correlation` all return the richer dict.
- **Do not change `append()` signature yet** — that's commit 3. This commit is additive-only at the storage layer.
- Run `poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py -v` — all 27 prompt-03 tests must still pass (the new columns have defaults, so old test fixtures still validate). Fix any failures before committing.
- Commit message: `phase 04-1: event log schema migration + envelope model (per D16)`.

**Commit 2 — schema registry.**
- Fill in `adminme/events/registry.py` per the API below.
- Create `adminme/events/schemas/__init__.py` (empty module-level docstring explaining the per-family file layout).
- Write `tests/unit/test_schema_registry.py` with at least 8 unit tests (enumerated below).
- Run the new tests; confirm they pass. Prompt-03 tests must still pass.
- Commit message: `phase 04-2: schema registry + upcaster plumbing (per D7)`.

**Commit 3 — 15 canonical schemas + append() signature shift + validation integration.**
- Write each of the 15 schemas listed below, grouped by file. Each module calls `registry.register(...)` at import time.
- Update `adminme/events/log.py::append()` signature per D8 addition 2: `async def append(self, envelope: EventEnvelope, *, correlation_id: str | None = None, causation_id: str | None = None) -> str`. `append_batch` becomes `async def append_batch(self, envelopes: list[EventEnvelope], *, correlation_id: str | None = None, causation_id: str | None = None) -> list[str]`. For batch, the correlation/causation applies to every envelope in the batch (rationale: a batch represents one logical operation; caller who wants different values across events calls `append` per event).
- Wire validation into `append()`: after envelope assembly and before insert, call `registry.validate(envelope.type, envelope.schema_version, envelope.payload)`. If the registry raises `SchemaNotFound`, behavior depends on `ADMINME_ALLOW_UNKNOWN_SCHEMAS` environment variable: default (unset or `0`) → raise `EventValidationError`; set to `1` → log warning and proceed. If the registry raises `pydantic.ValidationError`, wrap in `EventValidationError` and re-raise.
- Update prompt-03 tests that use the old `append(dict)` signature: rewrite them to build an `EventEnvelope` first. This touches `tests/unit/test_event_log.py` and `tests/unit/test_event_bus.py`. Also update `scripts/demo_event_log.py`.
- Write `tests/unit/test_event_validation.py` with at least 15 test cases (one valid + one invalid per schema is overkill for this commit — minimum 15; extend in later prompts).
- Run full verification block. If any prompt-03 test fails and you cannot fix it trivially by rewriting the test to use `EventEnvelope`, stop and report — something deeper is wrong.
- Commit message: `phase 04-3: 15 canonical schemas + append() takes EventEnvelope + D8 kwargs`.

**Commit 4 — demo update + verification + push.**
- Extend `scripts/demo_event_log.py` to exercise typed events: append 10 `party.created`, 10 `commitment.proposed`, attempt 1 invalid `commitment.proposed` (wrap in `try/except EventValidationError` and print the caught error), read them back, print summary.
- Run the full verification block below. Paste output into the commit message.
- Commit message: `phase 04-4: demo using typed events + verification`.
- `git push origin HEAD`.

**If a turn times out mid-section:** STOP. Do not attempt heroic recovery. The operator resets; the next session reads `git log --oneline` and picks up from the next batch.

---

## Deliverables

### `adminme/events/migrations/0002_full_envelope.sql`

Additive migration to the schema shipped in 0001. Adds the columns SYSTEM_INVARIANTS.md §1 invariant 5 and BUILD.md §L2 specify. Every new NOT NULL column has a DEFAULT that back-fills any rows already present (see commit-1 guidance above for the backfill strategy per column).

Explicit columns to add:
- `schema_version INTEGER NOT NULL DEFAULT 1`
- `occurred_at TEXT NOT NULL DEFAULT ''` (the migration updates this from `event_at_ms` via an `UPDATE events SET occurred_at = ...` step immediately after the ALTER, then an `ALTER` removing the DEFAULT is unnecessary — SQLite can't remove defaults anyway; leaving it at `''` is acceptable)
- `recorded_at TEXT NOT NULL DEFAULT ''` (same treatment)
- `source_adapter TEXT NOT NULL DEFAULT 'unknown:legacy'`
- `source_account_id TEXT NOT NULL DEFAULT 'legacy'`
- `visibility_scope TEXT NOT NULL DEFAULT ''` (backfilled to `owner_scope`)
- `sensitivity TEXT NOT NULL DEFAULT 'normal'`
- `causation_id TEXT`
- `raw_ref TEXT`
- `actor_identity TEXT`

Add one new index:
- `CREATE INDEX IF NOT EXISTS idx_events_causation ON events (causation_id) WHERE causation_id IS NOT NULL;`

The `IF NOT EXISTS` on both ALTER TABLE (via a trick — wrap each ALTER in a `PRAGMA table_info` check and skip if the column exists) and the index keeps the migration idempotent per the `_schema_version` registry.

Rename the `version` column? **NO.** SQLite does not support column rename pre-3.25 cleanly, and we have no need to rename — it already matches `schema_version` semantically. Add `schema_version` as a new column with the same values; leave `version` untouched; let later prompts (17, the CLI cleanup) drop `version` if they want. **Consistency note:** the EventEnvelope model exposes `schema_version` only; the `version` column is a legacy/alias column that the migration initializes equal to `schema_version` and no new code reads.

Actually — simpler: **drop the `version` column from reads entirely in this prompt.** The `_row_to_event` function returns `schema_version`; `version` is never exposed. Both columns persist on disk as equal integers; future migration can drop `version` once no code references it.

### `adminme/events/envelope.py`

Typed envelope model. Shape:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Sensitivity = Literal["normal", "sensitive", "privileged"]


class EventEnvelope(BaseModel):
    """
    Typed envelope for every event in the log.

    Per ADMINISTRATEME_BUILD.md §L2, SYSTEM_INVARIANTS.md §1 invariant 5,
    and DECISIONS.md §D16. Field names match BUILD.md §L2 row schema.

    `payload` is validated by the schema registry against the model
    registered for `(type, schema_version)`. Unknown types are rejected
    unless ADMINME_ALLOW_UNKNOWN_SCHEMAS=1.
    """

    model_config = {"extra": "forbid"}

    event_id: str = ""                      # assigned by EventLog.append if empty
    event_at_ms: int                        # unix ms — prompt-03 field, preserved for sort order
    tenant_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    occurred_at: str                        # ISO 8601 UTC
    recorded_at: str = ""                   # ISO 8601 UTC — filled by append() if empty
    source_adapter: str = Field(min_length=1)   # e.g. "messaging:gmail_api"
    source_account_id: str = Field(min_length=1)
    owner_scope: str = Field(min_length=1)  # "private:<member_id>" | "shared:household" | "org:<id>"
    visibility_scope: str = Field(min_length=1)
    sensitivity: Sensitivity = "normal"
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any]
    raw_ref: str | None = None
    actor_identity: str | None = None

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _iso_utc_or_empty(cls, v: str) -> str:
        if v == "":
            return v
        # Parse permissively — any string datetime.fromisoformat can accept
        # is OK. We don't require trailing Z or microseconds.
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"not a valid ISO 8601 datetime: {v}") from exc
        return v
```

Add a helper `EventEnvelope.now_utc_iso() -> str` returning the current UTC time in ISO 8601 with seconds precision, for `occurred_at` / `recorded_at` defaults used by tests and the demo.

### `adminme/events/registry.py`

```python
"""
Schema registry — maps (event_type, schema_version) to payload Pydantic models.

Per ADMINISTRATEME_BUILD.md §L2 "Typed event registry",
SYSTEM_INVARIANTS.md §1 invariants 3 + 9, and DECISIONS.md §D7.

Upcasters are pure functions `upcast_v{N}_to_v{N+1}(payload: dict) -> dict`
that compose in order when reading an old event whose schema has since
been upgraded. This prompt defines the plumbing; no upcasters ship here.

Plugin-introduced event types register via the `hearth.event_types`
entry point per DECISIONS.md §D9 — the `autoload()` method walks that
entry-point group in addition to `adminme.events.schemas`.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


Upcaster = Callable[[dict[str, Any]], dict[str, Any]]


class RegistryError(RuntimeError):
    pass


class SchemaNotFound(RegistryError):
    def __init__(self, event_type: str, version: int) -> None:
        self.event_type = event_type
        self.version = version
        super().__init__(f"no schema for {event_type!r} v{version}")


class EventValidationError(RuntimeError):
    """Raised when an event's payload fails validation. Wraps pydantic.ValidationError."""

    def __init__(self, event_type: str, version: int, original: Exception) -> None:
        self.event_type = event_type
        self.version = version
        self.original = original
        super().__init__(f"payload validation failed for {event_type} v{version}: {original}")


class SchemaRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], type[BaseModel]] = {}
        self._upcasters: dict[tuple[str, int], Upcaster] = {}  # keyed by (type, from_version)

    def register(
        self,
        event_type: str,
        version: int,
        model: type[BaseModel],
    ) -> None:
        key = (event_type, version)
        if key in self._by_key:
            raise RegistryError(f"duplicate registration: {event_type} v{version}")
        self._by_key[key] = model

    def register_upcaster(
        self,
        event_type: str,
        from_version: int,
        upcaster: Upcaster,
    ) -> None:
        """Register an upcaster that transforms v{from_version} payload to v{from_version+1}."""
        key = (event_type, from_version)
        if key in self._upcasters:
            raise RegistryError(f"duplicate upcaster: {event_type} v{from_version}→v{from_version+1}")
        self._upcasters[key] = upcaster

    def get(self, event_type: str, version: int) -> type[BaseModel] | None:
        return self._by_key.get((event_type, version))

    def known_types(self) -> list[str]:
        return sorted({t for (t, _) in self._by_key})

    def latest_version(self, event_type: str) -> int | None:
        versions = [v for (t, v) in self._by_key if t == event_type]
        return max(versions) if versions else None

    def validate(self, event_type: str, version: int, payload: dict[str, Any]) -> BaseModel:
        model = self.get(event_type, version)
        if model is None:
            raise SchemaNotFound(event_type, version)
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise EventValidationError(event_type, version, exc) from exc

    def upcast(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        from_version: int,
        to_version: int,
    ) -> dict[str, Any]:
        if to_version < from_version:
            raise RegistryError(
                f"downcasting not supported: {event_type} v{from_version}→v{to_version}"
            )
        current = payload
        for v in range(from_version, to_version):
            up = self._upcasters.get((event_type, v))
            if up is None:
                raise RegistryError(
                    f"no upcaster registered for {event_type} v{v}→v{v+1}"
                )
            current = up(current)
        return current

    def autoload(self) -> None:
        """Import every schema module under adminme.events.schemas and every
        hearth.event_types entry point. Each module is expected to call
        registry.register(...) at import time."""
        import adminme.events.schemas as schemas_pkg  # type: ignore[import-untyped]

        for _, name, _ in pkgutil.walk_packages(
            schemas_pkg.__path__, prefix="adminme.events.schemas."
        ):
            importlib.import_module(name)

        try:
            eps = importlib.metadata.entry_points(group="hearth.event_types")
        except TypeError:  # Python <3.10 compat; we are on 3.11 per D10, so this shouldn't hit
            eps = importlib.metadata.entry_points().get("hearth.event_types", [])
        for ep in eps:
            ep.load()


# Module-level singleton for convenience. Tests construct fresh instances.
registry = SchemaRegistry()
```

### `adminme/events/schemas/*.py`

15 canonical schemas grouped across four files.

**`adminme/events/schemas/ingest.py`** — 5 schemas, adapter-emitted (L1):

- `messaging.received` v1 — `{source_channel: str, from_identifier: str, to_identifier: str, thread_id: str | None, subject: str | None, body_text: str | None, body_html: str | None, received_at: str (ISO), attachments: list[dict]}`. See BUILD.md §L1 messaging adapters.
- `messaging.sent` v1 — `{source_channel: str, to_identifier: str, thread_id: str | None, subject: str | None, body_text: str | None, sent_at: str (ISO), delivery_status: Literal["queued", "sent", "failed"]}`.
- `telephony.sms_received` v1 — `{from_number: str (E.164), to_number: str (E.164), body: str, received_at: str (ISO), carrier_message_id: str | None}`.
- `calendar.event_added` v1 — `{source: str (adapter id), external_event_id: str, calendar_id: str, summary: str, start: str (ISO), end: str (ISO), location: str | None, attendees: list[dict], body: str | None}`.
- `artifact.received` v1 — `{source: str, external_artifact_id: str | None, mime_type: str, size_bytes: int (ge=0), filename: str | None, sha256: str (64 hex chars), artifact_ref: str, received_at: str (ISO)}`.

**`adminme/events/schemas/crm.py`** — 4 schemas:

- `party.created` v1 — `{party_id: str, kind: Literal["person","organization","household"], display_name: str, sort_name: str, nickname: str | None, pronouns: str | None, notes: str | None, attributes: dict}`.
- `identifier.added` v1 — `{identifier_id: str, party_id: str, kind: str, value: str, value_normalized: str, verified: bool, primary_for_kind: bool}`.
- `membership.added` v1 — `{membership_id: str, party_id: str, parent_party_id: str, role: str, started_at: str | None (ISO), attributes: dict}`.
- `relationship.added` v1 — `{relationship_id: str, party_a: str, party_b: str, label: str, direction: Literal["a_to_b","b_to_a","mutual"], since: str | None (ISO), attributes: dict}`.

**`adminme/events/schemas/domain.py`** — 5 schemas:

- `commitment.proposed` v1 — model exactly after REFERENCE_EXAMPLES.md §5 (see the example block in the original prompt for field list; copy it verbatim with the Literals for `kind` and `strength`, the `confidence: float` with `ge=0 le=1`, the `suggested_due: datetime | None`). Provenance lives in `envelope.source`, not here.
- `commitment.confirmed` v1 — `{commitment_id: str, confirmed_by_member_id: str, confirmed_at: str (ISO), note: str | None}`.
- `task.created` v1 — `{task_id: str, title: str, description: str | None, owner_member_id: str | None, due: str | None (ISO), energy: Literal["low","medium","high"] | None, effort_min: int | None (ge=0), source_commitment_id: str | None}`.
- `task.completed` v1 — `{task_id: str, completed_by_member_id: str, completed_at: str (ISO), note: str | None}`.
- `skill.call.recorded` v2 — `{skill_name: str, skill_version: str, openclaw_invocation_id: str, inputs: dict, outputs: dict, provider: str, input_tokens: int (ge=0), output_tokens: int (ge=0), cost_usd: float (ge=0), duration_ms: int (ge=0)}`. **Note on v2:** per the prompt-04 spec, v2 is the first version AdministrateMe emits; v1 is reserved for pre-OpenClaw era events that do not exist in this log. We do not register a v1 model. `registry.latest_version("skill.call.recorded")` returns `2`.

**`adminme/events/schemas/governance.py`** — 1 schema:

- `observation.suppressed` v1 — `{attempted_action: str, attempted_at: str (ISO), target_channel: str, target_identifier: str, would_have_sent_payload: dict, reason: Literal["observation_mode_active","governance_review","governance_deny","governance_hard_refuse","rate_limit"], session_correlation_id: str | None}`.

Each file imports `from adminme.events.registry import registry` and ends with one `registry.register(...)` call per schema. Each schema uses `model_config = {"extra": "forbid"}` so unknown fields fail loudly.

### Wire validation into `EventLog.append()`

In `adminme/events/log.py`:

1. Add `async def append(self, envelope: EventEnvelope, *, correlation_id: str | None = None, causation_id: str | None = None) -> str` as the new public signature. The old `async def append(self, event: dict) -> str` is REMOVED — not deprecated, removed. All callers updated in this commit.

2. `append()` does, in order:
   a. If `envelope.event_id == ""`, mint one via `_EventIdGenerator`.
   b. If `envelope.recorded_at == ""`, set it to `EventEnvelope.now_utc_iso()`.
   c. Apply kwarg overrides: if `correlation_id is not None`, set envelope.correlation_id. Same for causation_id. (Kwargs override envelope fields — the kwargs are the canonical source per D8.)
   d. Call `registry.validate(envelope.type, envelope.schema_version, envelope.payload)` — on `SchemaNotFound`, check `ADMINME_ALLOW_UNKNOWN_SCHEMAS`; if `"1"`, log a warning and proceed; otherwise, raise. On `EventValidationError`, re-raise.
   e. Serialize envelope to the row tuple (all 15 columns now).
   f. Acquire write lock; `asyncio.to_thread(self._insert_rows, [row])`.
   g. Return `event_id`.

3. `append_batch` takes `list[EventEnvelope]` plus the same kwargs, validates each, inserts in one transaction.

4. `_row_to_event` returns a dict including all 15 columns. Consider returning an `EventEnvelope` directly instead of a dict — cleaner. If you do, update `read_since`, `get`, `get_by_correlation`, and `_SubscriberState` callback typing accordingly. Not strictly required by the prompt, but clean.

### Tests

**`tests/unit/test_schema_registry.py`** — at least 8 tests:

1. `register` + `get` roundtrip.
2. Duplicate registration raises `RegistryError`.
3. `latest_version` returns the max registered.
4. `validate` returns a model instance on valid payload.
5. `validate` raises `EventValidationError` on invalid payload (missing required field).
6. `validate` raises `SchemaNotFound` for unregistered `(type, version)`.
7. `register_upcaster` + `upcast` composes across multiple versions.
8. `upcast` with no registered upcaster raises.
9. `autoload` picks up every module under `adminme.events.schemas`. Assert at least 15 `(type, version)` pairs registered after autoload.

**`tests/unit/test_event_validation.py`** — at least 15 tests:

- One "valid → append succeeds" per schema (15 cases). Use `EventEnvelope.now_utc_iso()` for timestamps.
- At least 5 "invalid → append raises `EventValidationError`" cases (pick five schemas, violate a required field or a bounded value).
- One "unknown schema, strict mode → raises" test.
- One "unknown schema, `ADMINME_ALLOW_UNKNOWN_SCHEMAS=1` → warns and proceeds" test (use `monkeypatch.setenv`).
- One "skill.call.recorded v1 is not registered; latest_version returns 2; appending with version=2 succeeds" test — confirms the v1 reserved-slot behavior.

**Update `tests/unit/test_event_log.py` and `tests/unit/test_event_bus.py`** — rewrite the `_event(...)` factory helper in both files to return `EventEnvelope` instances. All 27 prompt-03 tests continue to pass unchanged in meaning.

### Demo update

`scripts/demo_event_log.py`:

- Build 10 `party.created` envelopes, append them.
- Build 10 `commitment.proposed` envelopes, append them.
- Build 1 malformed `commitment.proposed` (e.g. `confidence=1.5`), wrap `append` in `try/except EventValidationError`, print the caught error.
- Read all events via `read_since()`, count by type, print.
- Exit 0 if all 20 valid events landed and the 1 invalid one was caught; nonzero otherwise.

---

## Verification (run at end of Commit 4)

```bash
# Lint + types
poetry run ruff check adminme/events/ adminme/lib/crypto.py
poetry run mypy adminme/events/ adminme/lib/crypto.py

# Prompt 03 tests still pass
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py -v

# New tests
poetry run pytest tests/unit/test_schema_registry.py tests/unit/test_event_validation.py -v

# Whole unit suite
poetry run pytest tests/unit/ -v

# Canary stubs still present (skipped, not errored)
poetry run pytest tests/unit/test_no_hardcoded_identity.py tests/unit/test_no_hardcoded_instance_path.py -v

# Invariant check: still no provider SDK
grep -iE "^anthropic|^openai|anthropic =|openai =" pyproject.toml && echo "VIOLATION of §8" || echo "OK: no provider SDK dependencies"

# Invariant check: still no hardcoded instance paths
grep -rn "~/.adminme\|'/.adminme\|\"/.adminme\|os.path.expanduser.*\\.adminme" adminme/ bootstrap/ packs/ --include='*.py' --include='*.sh' || echo "OK: no hardcoded instance paths found"

# Demo
poetry run python scripts/demo_event_log.py
```

Expected:
- Ruff: `All checks passed!`
- Mypy: `Success: no issues found`
- Prompt 03 tests: 27 passed.
- New schema-registry tests: 9 passed.
- New event-validation tests: 20+ passed (15 valid + 5 invalid + 3 edge cases).
- Full unit suite: 50+ passed, 2 skipped (the canary stubs).
- Both invariant checks: OK.
- Demo: prints "caught validation error as expected" line, total event count 20.

If any check fails, fix before the final push.

---

## Final push

```bash
git log --oneline | head -6   # expect 4 phase 04-N commits on top of the 03.5 merge
git status                    # expect clean working tree
git push origin HEAD
```

Do NOT open the PR. The operator opens, reviews, merges.

---

## Stop

**Explicit stop message to the operator:**

```
Schema layer in. 15 canonical event types modeled. Registry validates on append.
Envelope migrated to BUILD.md §L2 full shape per D16. append() signature shifted
per D8 addition 2. All prompt-03 tests updated to use EventEnvelope and still pass.

Branch: <harness-assigned branch name>
Commits: phase 04-1 through phase 04-4 on top of main.

Verification summary:
- poetry install: <success/failure>
- ruff / mypy: <clean/errors>
- prompt 03 tests: <N passed, 0 failed>
- schema_registry tests: <N passed>
- event_validation tests: <N passed>
- full unit suite: <N passed, 2 skipped>
- provider SDK check: OK
- hardcoded path check: OK
- demo: <produced expected output with 1 caught validation error>

Ready for prompt 05 (projections core: parties, interactions, artifacts)
once this branch is reviewed and merged.
```

Then STOP. Do not open the PR. Do not push to main. Do not proceed to prompt 05.
