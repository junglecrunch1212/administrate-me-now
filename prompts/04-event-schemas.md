# Prompt 04: Event schemas + schema registry

**Phase:** BUILD.md PHASE 1 (schema half) and PHASE 2 (schema registry).
**Depends on:** Prompt 03 passed. Event log + bus operational.
**Estimated duration:** 2-3 hours.
**Stop condition:** All event types defined as Pydantic models; registry validates events on append; replay is schema-version-aware; >90% of events emitted in later prompts will validate against these schemas.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` sections:
   - The **event taxonomy** table(s) — use grep for "event.type" entries; BUILD.md lists many in terminology and L1-L4 sections.
   - **"EVENT VERSIONING"** (or similar heading about version compatibility).
   - Every L1 adapter section, L4 pipeline section, and L5 surface section — these all emit events; each lists the events it emits.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §5 (`commitment.proposed` event type). This is the canonical example of a fully-specified event schema.
3. `docs/architecture-summary.md` §3 (your own summary from prompt 01).
4. `adminme/events/log.py` (from prompt 03) — you need to know exactly what shape events take at the storage layer.

## Operating context

Every event that AdministrateMe emits has a schema. The schema is a Pydantic model that validates the `payload` field of the event. The registry maps `type` strings (like `"commitment.proposed"`) to model classes, and validates events at append time.

This matters for two reasons:
1. **Correctness:** A typo in an event payload should fail loudly at write time, not produce a subtly-wrong projection days later.
2. **Replay:** When you upgrade a schema from v1 to v2, older events (stored with v1) still need to be readable. Schemas are versioned; the registry knows how to read v1 events even after v2 is the current.

This prompt builds the schema registry. It does NOT build every schema — that would be ~60 models and too much to do carefully in one session. Instead, you define:
- The **base event schema** (every event has these fields no matter what).
- A **representative set** of ~15 schemas covering every L1 adapter family, every L3 projection, and every pipeline kind. This proves the pattern works.
- The **registry itself**, which validates events on append.
- Integration with `EventLog.append()` — it now rejects events whose payload fails validation.

Later prompts (05-14) will define additional schemas as their sections are built. Each such prompt will add ~5 more schemas following the pattern established here.

## Objective

1. Define the base event envelope as a Pydantic model.
2. Build the schema registry that dispatches `type` → Pydantic model and handles versioning.
3. Implement ~15 canonical schemas (list below) that exercise every event pattern used in the system.
4. Wire validation into `EventLog.append()`.
5. Tests.

## Out of scope

- Do NOT define every event schema. Later prompts add their own.
- Do NOT implement schema migrations (upgrading an old v1 event in the log to v2 shape). Versioning means the reader knows how to interpret each version; events are never rewritten. Migration utilities are prompt 17.
- Do NOT emit events in this prompt. You're defining schemas; sending them is later.

## Deliverables

### `adminme/events/envelope.py`

The base model every event uses:

```python
from pydantic import BaseModel, Field
from typing import Literal

class EventEnvelope(BaseModel):
    """
    Per BUILD.md L2 §"event schema".
    Every event in the log has this shape. `payload` is the event-type-specific
    part; it's validated by the registry against a type-specific Pydantic model.
    """
    event_id: str                               # ev_xxxxx, assigned by EventLog.append
    event_at_ms: int                            # wall-clock millis since epoch
    tenant_id: str                              # household id
    owner_scope: Literal["household", "private"] | str  # "household" or "private:m-<member_id>"
    type: str                                   # e.g., "commitment.proposed"
    version: int                                # schema version for this type
    correlation_id: str | None = None
    source: dict | None = None                  # {skills: [...], source_event_id: "...", ...}
    payload: dict                               # validated against type-specific model

    model_config = {"extra": "forbid"}
```

### `adminme/events/registry.py`

```python
"""
Schema registry. Maps (type, version) -> Pydantic payload model.
Per BUILD.md "EVENT VERSIONING".
"""

from __future__ import annotations
import importlib
import pkgutil
from typing import Type
from pydantic import BaseModel, ValidationError

class SchemaRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], Type[BaseModel]] = {}

    def register(self, event_type: str, version: int, model: Type[BaseModel]) -> None:
        key = (event_type, version)
        if key in self._by_key:
            raise RegistryError(f"duplicate registration: {event_type} v{version}")
        self._by_key[key] = model

    def get(self, event_type: str, version: int) -> Type[BaseModel] | None:
        return self._by_key.get((event_type, version))

    def latest_version(self, event_type: str) -> int | None:
        versions = [v for (t, v) in self._by_key if t == event_type]
        return max(versions) if versions else None

    def validate(self, event_type: str, version: int, payload: dict) -> BaseModel:
        """
        Looks up the model for (type, version) and validates payload against it.
        Raises SchemaNotFound if the type/version isn't registered.
        Raises ValidationError (from Pydantic) if the payload is invalid.
        """
        model = self.get(event_type, version)
        if model is None:
            raise SchemaNotFound(event_type, version)
        return model.model_validate(payload)

    def autoload(self) -> None:
        """
        Walks adminme.events.schemas package, imports every submodule.
        Each submodule is expected to call registry.register(...) at import time.
        """
        import adminme.events.schemas as schemas_pkg
        for _, name, _ in pkgutil.walk_packages(schemas_pkg.__path__, prefix="adminme.events.schemas."):
            importlib.import_module(name)

class SchemaNotFound(Exception):
    def __init__(self, event_type: str, version: int):
        self.event_type = event_type
        self.version = version
        super().__init__(f"no schema for {event_type} v{version}")

class RegistryError(Exception):
    pass

# Module-level singleton for convenience; tests use fresh instances.
registry = SchemaRegistry()
```

### `adminme/events/schemas/*.py`

Define exactly these **15 canonical schemas** (one per file, grouped sensibly):

**Ingest / adapter-emitted (`adminme/events/schemas/ingest.py`):**
- `messaging.received` v1 — inbound message from any channel
- `messaging.sent` v1 — outbound confirmation (from OpenClaw or standalone adapter)
- `telephony.sms_received` v1
- `calendar.event_added` v1 — external calendar → internal projection
- `artifact.received` v1 — incoming file

**CRM / party-related (`adminme/events/schemas/crm.py`):**
- `party.created` v1
- `identifier.added` v1 — email, phone, handle added to a party
- `membership.added` v1 — member joining household
- `relationship.added` v1 — party-to-party relationship

**Pipeline-emitted domain (`adminme/events/schemas/domain.py`):**
- `commitment.proposed` v1 — see REFERENCE_EXAMPLES.md §5 for canonical example
- `commitment.confirmed` v1
- `task.created` v1
- `task.completed` v1
- `skill.call.recorded` v2 — see BUILD.md L4 Skill Runner section; v2 because v1 would have been the pre-OpenClaw version (version 1 exists as a stub for replay compatibility, even though no v1 events are in this prompt's log)

**Governance (`adminme/events/schemas/governance.py`):**
- `observation.suppressed` v1 — outbound intercepted by observation mode

**Also:** `adminme/events/schemas/__init__.py` is empty or just docs. Each module, on import, calls `registry.register(...)` for its schemas.

For each schema, write it as a Pydantic model that captures the fields BUILD.md specifies. If BUILD.md is vague on a field, make a sensible choice and add a comment `# TODO(prompt-NN): confirm with BUILD.md §Y`. Do not invent fields that aren't implied by BUILD.md.

Example — `adminme/events/schemas/domain.py` (excerpt for one schema):

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal
from adminme.events.registry import registry

class CommitmentProposedV1(BaseModel):
    """
    Per REFERENCE_EXAMPLES.md §5.
    Emitted by the commitment_extraction pipeline when a message implies
    an obligation the principal hasn't yet committed to.
    """
    model_config = {"extra": "forbid"}

    commitment_id: str                          # cmt_xxxxx, generated by pipeline
    kind: Literal["reply", "deliverable", "rsvp", "call_back", "thank_you", "other"]
    owed_by_member_id: str                      # stice-james etc.
    owed_to_party_id: str                       # p-kate etc.
    text_summary: str                           # ≤200 chars; human-readable
    suggested_due: datetime | None              # pipeline's best guess
    confidence: float = Field(ge=0.0, le=1.0)
    strength: Literal["weak", "moderate", "confident", "strong"]
    source_summary: str                         # short phrase about why
    # provenance lives in envelope.source, not here

registry.register("commitment.proposed", 1, CommitmentProposedV1)
```

### Wire into EventLog

Modify `adminme/events/log.py::EventLog.append()` to call `registry.validate(type, version, payload)` before inserting. If validation fails, raise `EventValidationError` wrapping the Pydantic error. The log does NOT write invalid events.

Exception: if the registry has no schema for that type, the log can either (a) reject with a loud warning (v1 strictness) or (b) accept with a `UnknownSchemaWarning` log line (v1 permissiveness). BUILD.md leans toward strict; default to (a). Override via env `ADMINME_ALLOW_UNKNOWN_SCHEMAS=1` for lab use.

### Tests

`tests/unit/test_schema_registry.py`:
- Register a schema; `get` returns it.
- Duplicate registration raises.
- `latest_version` returns the max.
- `validate` returns a model instance on valid payload.
- `validate` raises Pydantic ValidationError on invalid payload.
- `validate` raises SchemaNotFound for an unregistered type.
- `autoload` picks up all schema modules.

`tests/unit/test_event_validation.py`:
- For each of the 15 schemas: construct a valid event, append to log, read back, assert payload matches.
- For each of the 15: construct an INVALID event (missing required field, wrong type, out-of-range value), assert append raises.
- Append with `version=99` (unregistered version of a known type) raises.
- In lab mode with `ADMINME_ALLOW_UNKNOWN_SCHEMAS=1`, unknown type is warned but accepted.

### Update the demo

Extend `scripts/demo_event_log.py` (from prompt 03) to use typed events: append 10 `party.created` events, 10 `commitment.proposed` events, 1 invalid `commitment.proposed` (expect failure), read them back, print a summary.

## Verification

```bash
# Lint + types
poetry run ruff check adminme/events/
poetry run mypy adminme/events/

# Tests (prompt 03 tests still pass)
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py -v

# New tests
poetry run pytest tests/unit/test_schema_registry.py tests/unit/test_event_validation.py -v

# Demo
poetry run python scripts/demo_event_log.py

git add adminme/events/envelope.py adminme/events/registry.py adminme/events/schemas/
git add tests/unit/test_schema_registry.py tests/unit/test_event_validation.py scripts/demo_event_log.py
git commit -m "phase 04: event schemas + registry"
```

Expected:
- All prompt 03 tests still pass (you didn't break them).
- ~25 new tests pass.
- Demo produces expected output including one planned failure that's caught.

## Stop

**Explicit stop message:**

> Schema layer in. 15 canonical event types modeled. Registry validates on append. Later prompts will add their own schemas using the same pattern (each prompt's Deliverables section will name the new schemas). Ready for prompt 05 (projections core: parties, interactions, artifacts). Please confirm tests pass before proceeding.

Do not begin projection work in this session. That is prompt 05.
