# Prompt 05: Projections core (parties, interactions, artifacts)

**Phase:** BUILD.md PHASE 2 — CRM primitives at L3.
**Depends on:** Prompts 01a/01b/01c/02/03/03.5/04 merged to main. Event log speaks typed `EventEnvelope`. Registry validates on append. `party.created`, `identifier.added`, `membership.added`, `relationship.added`, `messaging.received`, `messaging.sent`, `telephony.sms_received`, `artifact.received` schemas registered.
**Estimated duration:** 3.5–5 hours across four batch commits.
**Stop condition:** Three projections consume events, expose query functions, survive rebuild-from-log; runner discovers and dispatches; all prompt-03/04 tests still pass; ~25 new tests pass; a mini smoke script shows end-to-end fan-out from append → projection read.

---

## Phase + repository + documentation + sandbox discipline

You are in Phase A: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that would require those are marked `@pytest.mark.requires_live_services` and skipped.

Sandbox egress is allowlisted. `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 — expected, move on.

**Session start (required sequence):**

```bash
git checkout main
git pull origin main
git checkout -b phase-05-projections-core
# (harness may override with claude/<random>; work on whatever branch gets assigned)
```

**Verify prerequisites on main:**

```bash
ls -la docs/SYSTEM_INVARIANTS.md docs/DECISIONS.md docs/architecture-summary.md \
       docs/openclaw-cheatsheet.md docs/reference/_manifest.yaml \
       adminme/events/log.py adminme/events/bus.py adminme/events/envelope.py \
       adminme/events/registry.py adminme/events/schemas/crm.py \
       adminme/events/schemas/ingest.py
```

Confirm `docs/DECISIONS.md` contains D13-D16 (grep for `### D16`). If any is missing, STOP — prompt 03.5 or 04 did not merge.

---

## Read first (required, in this order)

1. **`docs/DECISIONS.md`** — full. Pay particular attention to **D2** (CRM is a shared L3 concern, not a product concern — this shapes who may read these projections), **D4** (products own surfaces, projections own data, events move state), **D7/D16** (schema versioning and the TEXT event_id), **D13** (sqlcipher3-binary), **D14** (async via `to_thread`), **D15** (instance-path discipline — **every projection DB path routes through InstanceConfig**).

2. **`docs/SYSTEM_INVARIANTS.md`** — full. Pay particular attention to:
   - **§2** (projections are derived, never truth — 7 invariants, all binding here).
   - **§3** (CRM spine — 7 invariants about parties/interactions/artifacts specifically).
   - **§6 invariant 4** (scope predicates auto-append; every projection test ships a canary that expects `ScopeViolation` on out-of-scope reads).
   - **§6 invariant 9** (privileged events never enter `vector_search` — not this prompt's concern, but the broader discipline starts here).
   - **§12 invariant 4** (no hardcoded tenant identity in platform code).

3. **`docs/architecture-summary.md`** §4 — the 11-projection table. Rows 3.1, 3.2, 3.3 are this prompt's scope. Skim the other rows for context on how later projections differ.

4. **`adminme/events/envelope.py`** and **`adminme/events/registry.py`** — the types you consume. Projections subscribe via the bus, receive `EventEnvelope` instances (or dicts — whichever prompt 04 settled on in its commit 3), and dispatch on `envelope.type`.

5. **`adminme/lib/instance_config.py`** — the stub from prompt 02. Prompt 03 built the event-log path resolution inline; this prompt **finishes the `InstanceConfig` implementation** because three projection databases now need paths and we cannot hardcode. Specifically:
   - `load_instance_config(instance_dir: Path) -> InstanceConfig` reads `<instance_dir>/config/instance.yaml` (or returns defaults keyed off `instance_dir` if the file is absent, which is the case for tests).
   - `InstanceConfig` exposes resolved `projection_db_path(projection_name: str) -> Path` that returns `<instance_dir>/projections/<projection_name>.db`.
   - `resolve_instance_dir() -> Path` reads `ADMINME_INSTANCE_DIR` env var; raises if unset (no default — production code never falls back to `~/.adminme/` silently).

6. **`ADMINISTRATEME_BUILD.md`** — three targeted sections:
   - **§L3: PROJECTIONS** (before the numbered subsections).
   - **§3.1 `parties` projection** — full table definitions.
   - **§3.2 `interactions`** and **§3.3 `artifacts`** — full table definitions.

7. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §4** — the canonical `parties` projection worked example. Schema, handlers, queries — model this prompt's three projections on that structure.

8. **`ADMINISTRATEME_DIAGRAMS.md`** §1 and §2 — where projections sit, how events flow through them.

Do NOT read:
- Sections covering later projections (commitments, tasks, recurrences, calendars — prompt 06).
- The ops-layer projections (money, xlsx_workbooks, vector_search, places_assets_accounts — prompt 07).
- L4 pipelines (prompts 10a-c).
- L5 surfaces (prompts 13, 14).

---

## Operating context

Projections are L3 read models. Each:

1. **Subscribes to a subset of event types** via the `EventBus` (prompt 03). One subscriber registration per projection.
2. **Writes rows into its own SQLite database.** Separate DB file per projection, resolved via `InstanceConfig`. Encrypted with SQLCipher using the same instance master key the event log uses.
3. **Exposes query functions** for L4 pipelines and L5 surfaces. Queries go through `Session` — but `Session` itself is prompt 08. This prompt exposes plain functions; prompt 08 wraps them.
4. **Is deterministic.** Same events in, same rows out. Apply order matters, apply idempotency matters. `rebuild()` truncates and replays from event 0 and produces byte-identical state.
5. **Never writes to the event log.** Projections consume, never emit.

Three projections this prompt:

- **`parties`** — the CRM spine. Persons, organizations, households. Tables: `parties`, `identifiers`, `memberships`, `relationships`.
- **`interactions`** — deduplicated touchpoints. One row may aggregate multiple raw events. Tables: `interactions`, `interaction_participants`, `interaction_attachments` (the last stays empty until prompt 06 wires artifacts in properly — stub table, no links yet).
- **`artifacts`** — documents, images, structured records. Table: `artifacts`. (`artifact_links` polymorphic table is stubbed; populated by prompt 06.)

Plus a **projection runner** that discovers, dispatches, and rebuilds.

The split across multiple DB files is deliberate: `rebuild(parties)` should not touch interactions' state, and two concurrent rebuild operations should not contend. Every projection has its own connection, its own WAL, its own checkpoint row in a shared `_projection_checkpoints.db`.

---

## Out of scope

- Do NOT build `commitments`, `tasks`, `recurrences`, `calendars` — prompt 06.
- Do NOT build `money`, `xlsx_workbooks`, `vector_search`, `places_assets_accounts` — prompt 07.
- Do NOT build L4 pipelines that would emit events into these projections — prompts 10a-c.
- Do NOT build `Session` / scope enforcement — prompt 08. Projections expose plain query functions; `Session` wraps them later. But: add a `# TODO(prompt-08): wrap with Session scope check` comment on every query function so prompt 08 knows where to insert.
- Do NOT implement `party.merged` handling — prompt 10b emits it; this prompt only registers a `party.merged` v1 schema stub in `adminme/events/schemas/crm.py` so future handlers have something to react to.
- Do NOT wire these projections into a FastAPI surface — products are prompts 13a/b.
- Do NOT ship plugin-provided projection support. Core-only.

---

## Incremental commit discipline — MANDATORY

Four batch commits. Same anti-timeout pattern as 01b, 01c, 02, 04.

**Commit 1 — `InstanceConfig` implementation + projection base + runner skeleton.**
- Fill in `adminme/lib/instance_config.py` per the spec above (`load_instance_config`, `resolve_instance_dir`, `projection_db_path`).
- Write `adminme/projections/base.py` — the `Projection` abstract base class (name, version, subscribes_to, schema_path, apply, after_batch).
- Write `adminme/projections/runner.py` — `ProjectionRunner(bus, instance_config, ...)` with start/stop/rebuild/status methods. Delegates fan-out to `EventBus`; owns the projection DB connections and one-shot rebuild.
- Update `tests/unit/test_no_hardcoded_instance_path.py` — prompt 05 is the earliest prompt where the canary actually has something to check. Implement the grep-based assertion (walk `adminme/`, `bootstrap/`, `packs/`; flag any `.py` or `.sh` file whose runtime string literals match the same regex set the verification block uses). Remove the `pytest.skip` decorator.
- Wire `InstanceConfig` into `adminme/events/log.py` — the log takes an `InstanceConfig` at construction now and derives its own path from `config.event_log_path`. Backward-compat: if a raw `Path` is passed in tests or legacy callers, keep accepting it, but deprecate with a warning. Update prompt-03 tests to pass `InstanceConfig` or tmp paths via the new helper.
- Run full prompt-03/04 test suite. Must pass.
- Commit message: `phase 05-1: InstanceConfig full implementation + projection base/runner`.

**Commit 2 — `parties` projection.**
- `adminme/projections/parties/schema.sql` — DDL for `parties`, `identifiers`, `memberships`, `relationships` per BUILD.md §3.1.
- `adminme/projections/parties/handlers.py` — one handler per subscribed event type (`party.created`, `identifier.added`, `membership.added`, `relationship.added`). Idempotent: re-applying same event produces same row state (use `INSERT ... ON CONFLICT DO UPDATE` keyed on primary key, or equivalent).
- `adminme/projections/parties/queries.py` — `get_party(party_id)`, `find_by_identifier(kind, value_normalized)`, `list_household_members(household_party_id)`, `relationships_of(party_id)`. Each function takes a read-only `sqlcipher3.Connection` as its first arg — no ambient session state.
- `tests/unit/test_projection_parties.py` — at least 10 tests covering happy path, idempotency, rebuild correctness, query edge cases.
- Commit message: `phase 05-2: parties projection (CRM spine)`.

**Commit 3 — `interactions` + `artifacts` projections.**
- Symmetric structure to commit 2.
- `interactions` handles `messaging.received`, `messaging.sent`, `telephony.sms_received`. One row per `envelope.event_id` for v1 — dedup/aggregate semantics (the "one row may aggregate multiple raw events" contract) is a prompt-06-or-later refinement; flag as `# TODO(prompt-10b): noise_filtering pipeline will merge related interactions`.
- `artifacts` handles `artifact.received`. `artifact_links` table created empty, populated later.
- `tests/unit/test_projection_interactions.py` and `tests/unit/test_projection_artifacts.py` — ~7 tests each.
- Commit message: `phase 05-3: interactions + artifacts projections`.

**Commit 4 — integration test + rebuild correctness + smoke + verification + push.**
- `tests/integration/test_projection_rebuild.py` — the big one. Populate 500 mixed events, start runner, let projections catch up, call `rebuild("parties")`, assert post-rebuild state matches pre-rebuild state byte-for-byte on row data. Same for `interactions` and `artifacts`.
- `tests/integration/test_projection_scope_canary.py` — one canary test per projection that asserts privileged events are readable by their owner only. Since `Session` isn't implemented yet (prompt 08), this test is currently structural — it appends a `privileged` event and asserts the row lands in the DB with `sensitivity='privileged'` set correctly. Prompt 08 extends the assertion to `with pytest.raises(ScopeViolation): read_as_other_member(...)`.
- `scripts/demo_projections.py` — mini end-to-end: spin up a tmp instance, register all three projections, append a scripted set of events (create a household, 3 members, 2 external parties, 5 messaging interactions, 2 artifacts), start runner, query each projection, print counts.
- Run the full verification block. Fix any failures.
- Commit message: `phase 05-4: rebuild correctness + scope canary + smoke + verification`.
- `git push origin HEAD`.

**If a turn times out mid-section:** STOP. Do not attempt recovery.

---

## Deliverables

### `adminme/lib/instance_config.py` (full implementation)

Replaces the stub from prompt 02. Public API:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class InstanceConfig:
    """Resolved instance-directory paths. Single source of truth per §15/D15."""

    instance_dir: Path
    tenant_id: str

    # Core event log
    event_log_path: Path
    bus_checkpoint_path: Path
    projection_checkpoint_path: Path

    # Projections directory (one .db file per projection lives under here)
    projections_dir: Path

    # Packs
    packs_dir: Path

    # Sidecars
    raw_events_dir: Path
    artifacts_dir: Path

    # Config + secrets
    config_dir: Path
    secrets_path: Path

    # xlsx workbook projections (prompt 07 uses these)
    xlsx_workbooks_dir: Path

    def projection_db_path(self, projection_name: str) -> Path:
        """Return the SQLite path for a named projection. Does not create the file."""
        return self.projections_dir / f"{projection_name}.db"


def resolve_instance_dir() -> Path:
    """Resolve the active instance directory from ADMINME_INSTANCE_DIR.

    Raises RuntimeError if the env var is unset. Production code never
    falls back to a default path; bootstrap sets the env var for every
    service it starts.
    """
    value = os.environ.get("ADMINME_INSTANCE_DIR")
    if not value:
        raise RuntimeError(
            "ADMINME_INSTANCE_DIR is not set. "
            "Tests must pass an explicit path to load_instance_config(); "
            "production services have it set by bootstrap (§15/D15)."
        )
    return Path(value)


def load_instance_config(instance_dir: Path) -> InstanceConfig:
    """Build an InstanceConfig from an instance directory.

    If <instance_dir>/config/instance.yaml exists, read tenant_id from it.
    Otherwise, synthesize a deterministic tenant_id from the directory name
    (test-friendly).
    """
    instance_dir = Path(instance_dir)
    config_dir = instance_dir / "config"
    config_yaml = config_dir / "instance.yaml"
    if config_yaml.exists():
        with config_yaml.open() as f:
            data = yaml.safe_load(f) or {}
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            raise RuntimeError(f"tenant_id missing from {config_yaml}")
    else:
        # Test-path default. Deterministic off the directory name.
        tenant_id = f"tenant-{instance_dir.name}"

    return InstanceConfig(
        instance_dir=instance_dir,
        tenant_id=tenant_id,
        event_log_path=instance_dir / "events" / "events.db",
        bus_checkpoint_path=instance_dir / "events" / "bus_checkpoints.db",
        projection_checkpoint_path=instance_dir / "projections" / "_checkpoints.db",
        projections_dir=instance_dir / "projections",
        packs_dir=instance_dir / "packs",
        raw_events_dir=instance_dir / "data" / "raw_events",
        artifacts_dir=instance_dir / "data" / "artifacts",
        config_dir=config_dir,
        secrets_path=instance_dir / "config" / "secrets.yaml.enc",
        xlsx_workbooks_dir=instance_dir / "projections" / ".xlsx-state",
    )
```

Note: all paths are computed from `instance_dir`, which is passed in explicitly. No module-level or function-default resolution back to `~/.adminme/`. This is the §15/D15 discipline in one file.

### `adminme/projections/base.py`

```python
"""
Projection protocol — the contract every L3 projection satisfies.

Per SYSTEM_INVARIANTS.md §2 and ADMINISTRATEME_BUILD.md §L3.

Each projection:
- Has a name (string) and a version (integer; bump to trigger rebuild).
- Subscribes to a list of event types (or ["*"]).
- Owns its own SQLite database under InstanceConfig.projections_dir.
- Exposes an idempotent apply(envelope, conn) per event.
- Exposes a rebuild() that drops its DB and replays from event 0.
- NEVER writes to the event log (§2 invariant 2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Projection(ABC):
    name: str
    version: int
    subscribes_to: list[str] | str  # list of event_type strings, or "*"
    schema_path: Path  # path to the projection's schema.sql (relative to module)

    @abstractmethod
    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        """Apply one event. Must be idempotent: re-applying the same event
        produces the same row state. Handlers that need cross-event state
        MUST read it from the projection's own DB, not from handler-local
        caches — rebuild correctness depends on it."""

    def after_batch(self, conn: Any) -> None:
        """Optional hook called after a batch of events has been applied.
        Default: no-op."""
        return None
```

### `adminme/projections/runner.py`

Spec:

```python
class ProjectionRunner:
    def __init__(
        self,
        bus: EventBus,
        log: EventLog,
        instance_config: InstanceConfig,
        *,
        encryption_key: bytes,
    ) -> None: ...

    def register(self, projection: Projection) -> None:
        """Register a projection. Must be called before start()."""

    async def start(self) -> None:
        """Open each projection's DB, apply schema.sql if new, subscribe to
        the bus with one callback per projection that dispatches to apply()."""

    async def stop(self) -> None:
        """Close DB connections. Checkpoint state is owned by the bus."""

    async def rebuild(self, projection_name: str) -> None:
        """Drop the projection's DB, recreate from schema.sql, replay the
        entire event log through apply(), reset the bus checkpoint."""

    async def status(self) -> dict:
        """Per-projection dict: name, version, row_counts, checkpoint,
        lag_count, last_event_applied."""
```

Subscription pattern: one bus subscriber per projection with `subscriber_id = f"projection:{name}"`. The callback is a closure that opens a transaction on the projection's DB, calls `projection.apply(envelope, conn)`, commits, advances checkpoint (via the bus's existing checkpoint persistence). The existing `EventBus` fan-out handles retry/degrade — no new code needed for that on the runner side.

### `adminme/projections/parties/*`

Three files plus `__init__.py`:

**`schema.sql`** — DDL per BUILD.md §3.1. Four tables: `parties`, `identifiers`, `memberships`, `relationships`. Column types match BUILD.md. Each table has a `last_event_id` or equivalent for projection-rebuild idempotency. `UNIQUE (kind, value_normalized)` on `identifiers` per §3 invariant / BUILD.md §3.1.

Do NOT embed tenant data. Schemas are tenant-agnostic — the tenant_id lives only in the envelopes the handlers consume (§12 invariant 4, D11).

**`handlers.py`** — one function per subscribed event type. Each function takes `(envelope_dict, conn)` and performs one UPSERT. Idempotent.

Schema-level guards to add:
- `identifier.added` — if `(kind, value_normalized)` already exists for a different `party_id`, emit nothing and log at INFO ("potential merge candidate; prompt 10a identity_resolution will handle"). Do not raise — projections never raise on data conflicts; they surface them via state.

**`queries.py`** — plain functions taking `(conn, ...)`. Each function has a `# TODO(prompt-08): wrap with Session scope check` comment on the line above the `def`.

Queries to implement:
- `get_party(conn, *, tenant_id, party_id) -> dict | None`
- `find_party_by_identifier(conn, *, tenant_id, kind, value_normalized) -> dict | None`
- `list_household_members(conn, *, tenant_id, household_party_id) -> list[dict]`
- `relationships_of(conn, *, tenant_id, party_id) -> list[dict]`
- `all_parties(conn, *, tenant_id, kind: str | None = None) -> list[dict]` (for the demo script)

Every query takes `tenant_id` as an explicit required keyword — §12 invariant 1. No global tenant context.

**`__init__.py`** — exports the `PartiesProjection(Projection)` class that binds name, version, subscribes_to, schema_path, and apply.

### `adminme/projections/interactions/*`

Same structure. Handles `messaging.received`, `messaging.sent`, `telephony.sms_received`. Table `interactions` with `raw_event_ids TEXT NOT NULL` (JSON array of event_id strings — for v1 every interaction row aggregates exactly one event, but the column is ready for prompt-10b noise-filtering dedup). Table `interaction_participants` with `(interaction_id, party_id, role)` primary key. `interaction_attachments` stubbed (created empty).

Queries:
- `recent_with(conn, *, tenant_id, party_id, days: int = 30) -> list[dict]`
- `thread(conn, *, tenant_id, thread_id: str) -> list[dict]`
- `closeness_signals(conn, *, tenant_id, party_id, since_iso: str) -> dict` (stub returning `{inbound_count: 0, outbound_count: 0, last_contact_iso: None}` for now — real implementation is prompt 05 or 06 but the signature stabilizes now so the parties projection can link against it).

### `adminme/projections/artifacts/*`

Same structure. Handles `artifact.received`. Table `artifacts` per BUILD.md §3.3. `artifact_links` table created empty (polymorphic links land in prompt 06).

Queries:
- `get_artifact(conn, *, tenant_id, artifact_id) -> dict | None`
- `search_by_sha256(conn, *, tenant_id, sha256) -> list[dict]`
- `list_recent(conn, *, tenant_id, limit: int = 100) -> list[dict]`

### `adminme/events/schemas/crm.py` — add `party.merged` v1 stub

Per prompt-04's note, this event type exists as a schema even though the pipeline that emits it lands in prompt 10b. Define:

```python
class PartyMergedV1(BaseModel):
    model_config = {"extra": "forbid"}

    surviving_party_id: str
    merged_party_id: str  # the one that gets collapsed
    merged_at: str  # ISO 8601 UTC
    merged_by_member_id: str | None = None
    rationale: str | None = None
```

Register with `registry.register("party.merged", 1, PartyMergedV1)`. No handler in this prompt — `parties` projection does not yet subscribe to `party.merged`. Prompt 10b wires subscription and applies merge logic.

### Canary test fill-in

`tests/unit/test_no_hardcoded_instance_path.py` — remove the `pytest.skip`. Implement:

```python
import re
from pathlib import Path

# Regex matches the runtime-literal patterns that violate §15/D15.
# Does NOT match docstring prose, which may legitimately reference the
# conceptual layout using ~/.adminme/ as an illustration.
FORBIDDEN_PATTERNS = [
    re.compile(r"['\"]~/\.adminme"),  # string literal starting with ~/.adminme
    re.compile(r"['\"]/\.adminme/"),   # string literal with /.adminme/
    re.compile(r"os\.path\.expanduser\([^)]*\.adminme"),
]

ALLOWED_DIRS_TO_SCAN = ["adminme", "bootstrap", "packs"]
EXEMPT_FILES = {
    # InstanceConfig itself can reference the literal in its docstring AND
    # in a single comment explaining the invariant. But: no runtime literal.
    # The regex above is already runtime-only; exemption list is empty by design.
}


def test_no_hardcoded_instance_path_in_platform_code():
    repo_root = Path(__file__).resolve().parents[2]
    violations = []
    for dir_name in ALLOWED_DIRS_TO_SCAN:
        root = repo_root / dir_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".sh"}:
                continue
            if str(path.relative_to(repo_root)) in EXEMPT_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            # Strip docstrings heuristically: remove triple-quoted blocks.
            stripped = re.sub(r'"""[\s\S]*?"""', "", text)
            stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
            for pat in FORBIDDEN_PATTERNS:
                for m in pat.finditer(stripped):
                    violations.append(f"{path}: {m.group(0)}")
    assert not violations, "Hardcoded instance-path literals found:\n" + "\n".join(violations)
```

Run the canary. It must pass — if it doesn't, something in prompts 02-05 violated §15. Fix the offending file before committing.

### Tests

**`tests/unit/test_projection_parties.py`** (≥10 tests):
- Apply `party.created`; row exists; re-apply; still one row.
- Apply `identifier.added`; row exists; unique constraint upheld on `(kind, value_normalized)`.
- Apply `membership.added`; row exists.
- Apply `relationship.added` with `direction="mutual"`; row exists; `relationships_of(party_a)` and `relationships_of(party_b)` both return it.
- `find_party_by_identifier` returns the right party.
- `list_household_members` returns exactly the memberships pointing at the household.
- Rebuild from a 50-event fixture; post-rebuild state byte-matches pre-rebuild.
- Unknown event types are ignored (not in subscribes_to); no error, no row.
- Multi-tenant isolation: events for `tenant-a` do not show up in queries scoped to `tenant-b`.
- Null-safe: `get_party` for nonexistent id returns None.

**`tests/unit/test_projection_interactions.py`** (≥7 tests):
- Apply `messaging.received`; interaction row exists with correct direction, channel_specific, participants.
- Apply `messaging.sent`; row exists with direction=outbound.
- Apply `telephony.sms_received`; row exists with channel_family=telephony.
- `recent_with` filters by date window.
- `thread` returns all interactions for a thread_id.
- Rebuild correctness.
- Multi-tenant isolation.

**`tests/unit/test_projection_artifacts.py`** (≥5 tests):
- Apply `artifact.received`; row exists.
- `search_by_sha256` returns matching artifacts.
- Idempotent re-application.
- Rebuild correctness.
- Multi-tenant isolation.

**`tests/integration/test_projection_rebuild.py`** (the big one):
- Populate event log with 500 mixed events across all three projections.
- Start runner; wait for catch-up.
- Snapshot all three projection DBs (row counts per table, plus a hash of sorted-by-PK row contents).
- Call `rebuild("parties")`; re-snapshot; assert equal.
- Call `rebuild("interactions")`; re-snapshot; assert equal.
- Call `rebuild("artifacts")`; re-snapshot; assert equal.

**`tests/integration/test_projection_scope_canary.py`** (stub per above):
- For each projection: append one `sensitivity="privileged"` envelope; apply; read the row; assert the `sensitivity` column is `'privileged'`. Prompt 08 extends this to the full `ScopeViolation` check.

### Smoke script

`scripts/demo_projections.py`:

```
Create tmp instance.
Derive InstanceConfig.
Build EventLog, EventBus, ProjectionRunner.
Register all three projections.
Append 10-15 events covering a realistic household (1 household + 3 members + 2 external parties + 5 messaging interactions + 2 artifacts).
Start runner. Wait 0.5s for catch-up.
Call status() on runner; print.
Query parties; print all by kind.
Query interactions for one party; print.
Query artifacts; print count.
Stop runner. Exit 0.
```

No user interaction. Runs in under 5 seconds on lab hardware.

---

## Verification (run at end of Commit 4)

```bash
# Lint + types
poetry run ruff check adminme/ tests/ scripts/
poetry run mypy adminme/

# Prompt 03/04 tests still pass
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py \
                 tests/unit/test_schema_registry.py tests/unit/test_event_validation.py -v

# Prompt 05 tests
poetry run pytest tests/unit/test_projection_*.py -v
poetry run pytest tests/integration/ -v

# Canary — should now PASS, not skip
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v
poetry run pytest tests/unit/test_no_hardcoded_identity.py -v  # still skipped — prompt 08 implements

# Full suite
poetry run pytest -v

# Invariant checks
grep -iE "^anthropic|^openai|anthropic =|openai =" pyproject.toml && echo "VIOLATION of §8" || echo "OK"
# (The new canary test itself covers the §15 check; the command below is belt-and-braces.)
grep -rn "~/.adminme\|'/.adminme\|\"/.adminme" adminme/ bootstrap/ packs/ --include='*.py' --include='*.sh' | grep -v "^docs/" || echo "OK: no hardcoded instance paths"

# Smoke
poetry run python scripts/demo_projections.py
```

Expected:
- Ruff: `All checks passed!`
- Mypy: `Success: no issues found`
- All prompt-03/04 tests: still passing (approx 50).
- Parties: ≥10 passed. Interactions: ≥7 passed. Artifacts: ≥5 passed.
- Integration rebuild: 3 tests passed (one per projection).
- Scope canary: 3 tests passed (one per projection; stub-level).
- Full suite: ~75 passed, 1 skipped (the identity canary; the instance-path canary is now implemented).
- Smoke: runs clean, prints expected counts.

---

## Final push

```bash
git log --oneline | head -6   # expect 4 phase 05-N commits on top of main
git status                    # expect clean working tree
git push origin HEAD
```

Do NOT open the PR. The operator opens, reviews, merges.

---

## Stop

**Explicit stop message to the operator:**

```
Three CRM projections in. Runner handles discovery, dispatch, and rebuild.
InstanceConfig fully implemented; §15/D15 canary now enforced in CI.

Branch: <harness-assigned branch name>
Commits: phase 05-1 through phase 05-4 on top of main.

Verification summary:
- ruff / mypy: clean
- prompt 03/04 tests: <N passed, 0 failed>
- parties / interactions / artifacts unit tests: <N/N/N passed>
- integration rebuild: 3 passed
- scope canary stub: 3 passed (prompt 08 extends to ScopeViolation)
- instance-path canary: PASSING (no longer skipped)
- identity canary: still skipped (prompt 08 or 17)
- smoke script: clean

Ready for prompt 06 (domain projections: commitments, tasks, recurrences,
calendars) once this branch is reviewed and merged.
```

Then STOP. Do not open the PR. Do not push to main. Do not proceed to prompt 06.
