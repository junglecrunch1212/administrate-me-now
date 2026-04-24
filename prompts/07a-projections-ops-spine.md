# Prompt 07a: Ops projections spine (money, places_assets_accounts, vector_search)

**Phase:** BUILD.md PHASE 2 continued — the third and final projection batch before xlsx_workbooks.
**Depends on:** Prompts 01a/01b/01c/02/03/03.5/04/05/06 merged to main. Seven projections live (parties/interactions/artifacts from 05; commitments/tasks/recurrences/calendars from 06). 30 event types registered at v1.
**Estimated duration:** 3.5–5 hours across four batch commits.
**Stop condition:** Three new projections (places_assets_accounts, money, vector_search) consume events, expose query functions, survive rebuild-from-log. 10 new event types registered at v1. Runner discovers and dispatches to all 10 of the sqlite-backed projections (05's 3 + 06's 4 + 07a's 3). Prompts 03/04/05/06 tests still pass. ≥30 new tests pass. Smoke script extended. **07b will add xlsx_workbooks forward daemon; 07c will add xlsx_workbooks reverse daemon. xlsx_workbooks projection is NOT built in this prompt.**

---

## Phase + repository + documentation + sandbox discipline

You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that would require those are marked `@pytest.mark.requires_live_services` and skipped.

Sandbox egress is allowlisted. github.com and raw.githubusercontent.com work. Most other hosts return HTTP 403 host_not_allowed — expected, move on.

**Session start (required sequence):**

```bash
git checkout main
git pull origin main
git checkout -b phase-07a-projections-ops-spine
# The harness may auto-reassign you to claude/<random> — work on whatever
# branch you actually get. Do NOT fight it. (Carry-forward CF-2.)
```

Do NOT `git pull` again during the session. Do NOT push to main. You will open a PR at the end. James reviews and merges it.

**Verify prerequisites on main:**

```bash
# Tier-2 docs present
ls -la docs/SYSTEM_INVARIANTS.md docs/DECISIONS.md docs/architecture-summary.md \
       docs/openclaw-cheatsheet.md docs/reference/_manifest.yaml

# Prompt 05/06 projection modules present
ls -d adminme/projections/parties adminme/projections/interactions \
      adminme/projections/artifacts adminme/projections/commitments \
      adminme/projections/tasks adminme/projections/recurrences \
      adminme/projections/calendars

# All-projection test run
poetry run pytest tests/unit/test_projection_*.py tests/unit/test_schema_registry.py \
                 tests/unit/test_event_validation.py -q 2>&1 | tail -3

# Canaries
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -3
# Expected: PASSING

# Event-type registry current shape
poetry run python -c "from adminme.events.registry import registry, ensure_autoloaded; \
  ensure_autoloaded(); \
  types = sorted(registry.known_types()); \
  print(f'total: {len(types)}'); \
  [print(t) for t in types]"
```

Expected event types on main after prompt 06: `artifact.received`, `calendar.event_{added,updated,deleted}`, `commitment.{proposed,confirmed,completed,dismissed,edited,snoozed,cancelled,delegated,expired}`, `identifier.added`, `membership.added`, `messaging.{received,sent}`, `observation.suppressed`, `party.{created,merged}`, `recurrence.{added,completed,updated}`, `relationship.added`, `skill.call.recorded`, `task.{created,completed,updated,deleted}`, `telephony.sms_received`. Total **30 types**. If any are missing or the count is off, **STOP** — a prior phase regressed.

**Env-requirement reality check:** `poetry install` may or may not be needed depending on the sandbox's warm state. If pytest fails with `ModuleNotFoundError: No module named 'sqlcipher3'`, run `poetry install 2>&1 | tail -5` and retry. This is a sandbox quirk; do not fix it in code.

---

## Read first (required, in this order)

**Read in the order listed. Every read uses a specific line range or a complete small file — no full reads of BUILD.md or REFERENCE_EXAMPLES.md, no full reads of docs/SYSTEM_INVARIANTS.md without a range. Context budget is load-bearing.**

1. **docs/DECISIONS.md** — full. Pay particular attention to **D4** (CRM including money/places/etc. is a shared L3 projection concern per [D4], not a product concern; Core/Comms/Automation are peers for reads), **D7** (new event types at v1), **D13** (sqlcipher3-binary), **D14** (async API, sync driver via asyncio.to_thread), **D15** (instance-path discipline).

2. **docs/SYSTEM_INVARIANTS.md** — targeted sections only:
   - `sed -n '20,34p' docs/SYSTEM_INVARIANTS.md` — **§1** (event log sacred).
   - `sed -n '35,46p' docs/SYSTEM_INVARIANTS.md` — **§2** (projections derived; 11 total per invariant 5). **§2.3 is load-bearing here:** projections trust events; cross-DB FK checks are documentation-only.
   - `sed -n '82,100p' docs/SYSTEM_INVARIANTS.md` — **§6** (privacy + sensitivity discipline, including [§6.3] `sensitivity='privileged'` never enters vector search).
   - `sed -n '110,130p' docs/SYSTEM_INVARIANTS.md` — **§8** (no direct LLM SDK imports; AdministrateMe does not embed locally — it calls out for embeddings, same as skills). Critical for vector_search.
   - `sed -n '181,191p' docs/SYSTEM_INVARIANTS.md` — **§12** (multi-tenant isolation).
   - `sed -n '192,206p' docs/SYSTEM_INVARIANTS.md` — **§13** (explicit non-connections — item 8 is load-bearing: `vector_search` MUST NOT include privileged content).
   - `sed -n '216,225p' docs/SYSTEM_INVARIANTS.md` — **§15** (instance-path discipline).

3. **docs/architecture-summary.md** — targeted: `sed -n '1,80p' docs/architecture-summary.md` — five-layer overview + projection table rows 3.8–3.10. No further reads needed from this file.

4. **ADMINISTRATEME_BUILD.md** — four targeted ranges:
   - `sed -n '715,772p' ADMINISTRATEME_BUILD.md` — **§3.8 places_assets_accounts** canonical DDL (three linked entity families: places, place_associations, assets, asset_owners, accounts).
   - `sed -n '773,795p' ADMINISTRATEME_BUILD.md` — **§3.9 money** canonical DDL (`money_flows` table).
   - `sed -n '796,810p' ADMINISTRATEME_BUILD.md` — **§3.10 vector_search** (sqlite-vec `vec0` virtual table). Note the embedding dimensions mention is model-dependent; you use a stub embedding function (see "Stub embedding" below).
   - `sed -n '1096,1120p' ADMINISTRATEME_BUILD.md` — **L3 continued: Session and scope enforcement**. Read this to understand what prompt 08 will layer onto your queries.

5. **adminme/projections/commitments/schema.sql**, **adminme/projections/commitments/handlers.py**, **adminme/projections/commitments/queries.py**, **adminme/projections/commitments/__init__.py** — full reads of all four. This is your quality-bar template from prompt 06. Copy: composite PK `(tenant_id, <entity_id>)`, cross-DB FK references as comments only, `INSERT ... ON CONFLICT DO UPDATE` idempotency, `# TODO(prompt-08): wrap with Session scope check` marker above every query def.

6. **adminme/projections/parties/queries.py** — targeted: `sed -n '1,80p' adminme/projections/parties/queries.py` — see how polymorphic lookups work (find_party_by_identifier pattern translates to vector_search's embedding lookup).

7. **adminme/events/schemas/domain.py** and **adminme/events/schemas/ingest.py** — full reads both. You will NOT add event types to domain.py or ingest.py in this prompt — the 07a event types are ops-flavored and get a new file. But read these to understand the registry-at-bottom convention.

8. **adminme/events/schemas/crm.py** — full read if it exists (it was set up in prompt 04 for party.merged and related). Confirms the per-file schema-registration pattern.

9. **tests/unit/test_projection_commitments.py** — targeted: `sed -n '1,120p' tests/unit/test_projection_commitments.py` — fixture pattern, `_envelope()` helper, `_wait_for_checkpoint()` helper, scope canary stub. Copy this structure for your new three projection test files.

10. **pyproject.toml** — targeted: `grep -E "sqlite-vec|openpyxl|numpy|sentence-transformers" pyproject.toml`. Confirm `sqlite-vec = "*"` is already declared (it was added in prompt 00's scaffold). `numpy` may be in already; check.

**Do NOT read** during this session:
- BUILD.md §3.11 (xlsx_workbooks) — **07b and 07c**. Ignore completely.
- BUILD.md §L4 onward — prompts 10a–c.
- ADMINISTRATEME_CONSOLE_PATTERNS.md — console is prompt 14.
- ADMINISTRATEME_REFERENCE_EXAMPLES.md — not needed for 07a.

---

## Operating context

You are writing three more sqlite-backed L3 projections on top of the runner/base/InstanceConfig scaffold from prompts 05 and 06. Same pattern, different domains:

- **places_assets_accounts** — three linked entity families in one projection. Places (home, office, school), assets (vehicle, appliance, boat, firearm), accounts (utility, bank, credit card, loan). Plus two association tables (`place_associations` for party↔place roles, `asset_owners` for party↔asset ownership). Event types: `place.added`, `place.updated`, `asset.added`, `asset.updated`, `account.added`, `account.updated`. Six event types.

- **money** — money flows. Every row is a transaction: `from_party → to_party`, `amount_minor` + `currency`, plus categorization and links (artifact, account, interaction). Event types: `money_flow.recorded` (adapter-emitted from Plaid, normalized receipts, etc.), `money_flow.manually_added` (principal added a row in xlsx — prompt 07c emits this), `money_flow.manually_deleted` (principal deleted a manual row). Three event types.

- **vector_search** — semantic index over non-privileged interaction summaries, artifact extracted_text, and party notes. Uses `sqlite-vec`'s `vec0` virtual table. Event type: `embedding.generated` (emitted by a separate embedding daemon in a later prompt; for now we register the schema and handler so the projection exists). **One event type.**

Each projection follows the prompt-05/06 pattern exactly:

1. **Subscribes to a subset of event types** via the EventBus. One subscriber per projection.
2. **Writes rows into its own SQLite database.** `<instance_dir>/projections/<name>.db`, resolved via `InstanceConfig.projection_db_path(name)`. SQLCipher-encrypted.
3. **Exposes query functions** taking `(conn, *, tenant_id, ...)` — plain functions; `# TODO(prompt-08): wrap with Session scope check` above each def.
4. **Is deterministic.** `INSERT ... ON CONFLICT DO UPDATE` idempotency.
5. **Never emits events.** Projections consume only ([§1.1], [§2.2]).

**Cross-DB FK note** (same as 06). The canonical DDL declares FK relationships — `place_associations.party_id REFERENCES parties(party_id)`, `asset_owners.party_id REFERENCES parties(party_id)`, `money_flows.linked_artifact REFERENCES artifacts(artifact_id)`, `accounts.organization REFERENCES parties(party_id)`, etc. SQLite cannot enforce cross-DB FKs. Keep the `REFERENCES` clauses as documentation; handlers don't validate; integration rebuild test logs orphans informationally per [§2.3].

**Stub embedding function for vector_search.** AdministrateMe does not import embedding SDKs directly per [§8]. In production, a separate embedding daemon (later prompt) calls out to OpenClaw's embedding endpoint and emits `embedding.generated` with the resulting vector. For prompt 07a, you implement the **consumer side only**: event handler stores the pre-computed vector from the payload. You do NOT implement the embedding daemon. Tests pass deterministic fake vectors (e.g. a 1536-dim vector derived from the sha256 of the source text) directly in the payload.

---

## Out of scope

- Do NOT build `xlsx_workbooks` forward daemon — **prompt 07b**.
- Do NOT build `xlsx_workbooks` reverse daemon — **prompt 07c**.
- Do NOT build L4 pipelines that emit these events (Plaid sync, OCR normalization, embedding generation) — prompts 10a/11.
- Do NOT import `anthropic`, `openai`, `sentence-transformers`, or any other embedding/LLM SDK into `adminme/` code ([§8], [D6]). The embedding daemon (future prompt) calls OpenClaw's skill runner over loopback — it does not link against a model directly.
- Do NOT build the CategoryLearner or assumption-projection pro-forma math — those are prompt 10c+ and prompt 07b's xlsx derived-sheet computation, respectively.
- Do NOT wire projections into any FastAPI router — prompts 13a/b.
- Do NOT ship plugin-provided projection support. Core-only.
- Do NOT implement Session / scope enforcement — **prompt 08**. Query functions ship plain; `# TODO(prompt-08)` marker on each.
- Do NOT reference tenant identity in `adminme/` — no "James", "Laura", "Charlie", "Stice", "Morningside" ([§12.4]). Tests/fixtures may use illustrative names.
- Do NOT add new external service clients to `pyproject.toml`.

---

## Carry-forwards from prompt 06 (fold in)

**CF-1 — gh CLI fallback:** `gh pr create` may not be available in the sandbox. Use it first; on failure, call the MCP tool `mcp__github__create_pull_request` with `base=main`, `head=<your branch>`, `owner=junglecrunch1212`, `repo=administrate-me-now`, title + body from the template at the end of this prompt.

**CF-2 — harness branch override:** The harness may auto-assign `claude/<random>` regardless of `git checkout -b`. Work on whatever branch you get. The branch name is recorded in the stop message.

**CF-3 — stop means stop:** When you call `mcp__github__create_pull_request`, the tool returns a webhook subscription message ("you'll now receive events for CI failures and review comments"). **Do NOT start polling.** Check CI status and reviews once (via `mcp__github__pull_request_read` with `method=get_status` and `get_reviews`). If both are empty or green, report and stop. Do NOT keep checking. Do NOT respond to any event that arrives after your stop message.

**CF-4 — mypy preflight for new libs:** If you add any import from a library not already in the codebase, run `poetry run mypy adminme/ 2>&1 | tail -10` before Commit 1. If it complains about missing stubs, add the library to the `[[tool.mypy.overrides]]` block in pyproject.toml with `ignore_missing_imports = true`. 07a is likely not to hit this because sqlite-vec and openpyxl are already in pyproject.toml. But check.

**CF-5 — async-subscriber test discipline:** Every test that appends an event and then reads the projection **must** call `notify(event_id)` on the bus and then `_wait_for_checkpoint(bus, subscriber_id, event_id)` before the read assertion. If a test is checking for something NOT landing (e.g. "privileged event skipped by vector_search"), append a follow-up innocuous event, notify its event_id, wait for checkpoint of that follow-up, THEN assert the original's absence. Without the follow-up the subscriber may not have processed the earlier event yet, and your "absence" assertion is just a timing artifact.

**CF-6 — CHECK-constraint style:** Prompt 06 was inconsistent (commitments had none, recurrences had some). For 07a, pick ONE style and apply uniformly: use `CHECK` for enum-valued columns wherever the spec specifies a closed enum (places.kind, assets.kind, accounts.kind, accounts.status, money_flows.kind). Do NOT add CHECK on open columns (category, display_name, etc.). Document the convention in a comment at the top of each schema.sql.

**New rule — BUILD_LOG append as a commit:** Commit 4 includes appending a structured entry to `docs/build_log.md` as part of the commit set. Template is at the bottom of this prompt. Do NOT hand-edit build_log.md in a separate operation — it's part of Commit 4's changeset and PR diff.

---

## Incremental commit discipline — MANDATORY

Four batch commits. If a turn times out mid-section: STOP. The operator re-launches.

### Commit 1 — Event-type registry expansion

Create a new file **`adminme/events/schemas/ops.py`** with 10 new Pydantic models and their registry calls. All register at **v1** per [D7]. This keeps domain.py and ingest.py uncluttered.

**Models:**

```python
# places
class PlaceAddedV1(BaseModel):
    place_id: str
    display_name: str
    kind: Literal["home", "second_home", "office", "school", "medical", "gym",
                  "church", "cemetery", "storage", "other"]
    address_json: dict[str, Any]  # street/city/state/postal/country
    geo_lat: float | None = None
    geo_lon: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

class PlaceUpdatedV1(BaseModel):
    place_id: str
    updated_at: str
    field_updates: dict[str, Any]

# assets
class AssetAddedV1(BaseModel):
    asset_id: str
    display_name: str
    kind: Literal["vehicle", "appliance", "instrument", "boat", "firearm", "pet", "other"]
    linked_place: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

class AssetUpdatedV1(BaseModel):
    asset_id: str
    updated_at: str
    field_updates: dict[str, Any]

# accounts
class AccountAddedV1(BaseModel):
    account_id: str
    display_name: str
    organization_party_id: str  # the counterparty (bank, utility)
    kind: Literal["utility", "subscription", "insurance", "license",
                  "bank", "credit_card", "loan", "brokerage", "other"]
    status: Literal["active", "dormant", "cancelled", "pending"] = "active"
    billing_rrule: str | None = None
    next_renewal: str | None = None
    login_vault_ref: str | None = None   # op:// or 1password:// — NEVER a password
    linked_asset: str | None = None
    linked_place: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

class AccountUpdatedV1(BaseModel):
    account_id: str
    updated_at: str
    field_updates: dict[str, Any]

# money_flow
class MoneyFlowRecordedV1(BaseModel):
    flow_id: str
    from_party_id: str | None = None
    to_party_id: str | None = None
    amount_minor: int   # smallest currency unit (cents for USD)
    currency: str = Field(pattern=r"^[A-Z]{3}$")  # ISO 4217
    occurred_at: str
    kind: Literal["paid", "received", "owed", "reimbursable"]
    category: str | None = None
    linked_artifact: str | None = None
    linked_account: str | None = None
    linked_interaction: str | None = None
    notes: str | None = None
    source_adapter: str   # 'plaid' | 'receipts_ocr' | 'manual' | ...

class MoneyFlowManuallyAddedV1(BaseModel):
    """Emitted by prompt 07c's xlsx reverse daemon when a principal adds a
    row in the Raw Data sheet with is_manual=TRUE. Separate event type from
    .recorded so downstream consumers can distinguish human-entered
    transactions from adapter-ingested ones.
    """
    flow_id: str
    from_party_id: str | None = None
    to_party_id: str | None = None
    amount_minor: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: str
    kind: Literal["paid", "received", "owed", "reimbursable"]
    category: str | None = None
    notes: str | None = None
    added_by_party_id: str

class MoneyFlowManuallyDeletedV1(BaseModel):
    """Emitted by prompt 07c when a principal deletes a manual row.
    Plaid-sourced rows cannot be deleted via xlsx (prompt 07c enforces)."""
    flow_id: str
    deleted_at: str
    deleted_by_party_id: str

# vector_search
class EmbeddingGeneratedV1(BaseModel):
    """Emitted by the embedding daemon (future prompt) after calling out
    to OpenClaw's embedding endpoint. The vector is pre-computed; this
    projection only stores, it does not embed. Per [§8], AdministrateMe
    itself does not import embedding SDKs.
    """
    embedding_id: str
    linked_kind: Literal["interaction", "artifact", "party_notes"]
    linked_id: str
    embedding_dimensions: int   # e.g. 1536 for text-embedding-3-small
    embedding: list[float]
    model_name: str             # 'text-embedding-3-small', 'voyage-3', etc.
    sensitivity: Literal["normal", "sensitive", "privileged"]  # denormalized
                                                                # for filter at handler time
    source_text_sha256: str
```

All models use `model_config = {"extra": "forbid"}`.

Register each with `registry.register("<type>", 1, <ModelClass>)`.

**Verify commit 1:**

```bash
poetry run pytest tests/unit/test_schema_registry.py -v 2>&1 | tail -5

poetry run python -c "
from adminme.events.registry import registry, ensure_autoloaded
ensure_autoloaded()
types = sorted(registry.known_types())
new_types = [t for t in types if any(p in t for p in [
    'place.added','place.updated','asset.added','asset.updated',
    'account.added','account.updated','money_flow.recorded',
    'money_flow.manually_added','money_flow.manually_deleted',
    'embedding.generated'])]
assert len(new_types) == 10, f'expected 10, got {len(new_types)}: {new_types}'
print(f'{len(new_types)} new types registered'); [print(t) for t in new_types]
print('---'); print(f'total types: {len(types)}')
"
# Expected: 10 types listed, total 40.

poetry run ruff check adminme/events/schemas/ops.py 2>&1 | tail -3
poetry run mypy adminme/events/schemas/ops.py 2>&1 | tail -3

git add adminme/events/schemas/ops.py
git commit -m "phase 07a-1: register 10 ops event types (places/assets/accounts/money/vector)"
```

If any assertion fails, STOP and fix before commit 2.

### Commit 2 — places_assets_accounts + money projections

Two projection modules, each with four files: `schema.sql`, `handlers.py`, `queries.py`, `__init__.py`. Match the prompt 06 pattern structurally.

#### `adminme/projections/places_assets_accounts/schema.sql`

Five tables per BUILD.md §3.8 verbatim, adjusted for multi-tenant:

- `places` — composite PK `(tenant_id, place_id)`. Columns per spec plus `visibility_scope`, `sensitivity`, `last_event_id`.
- `place_associations` — composite PK `(tenant_id, place_id, party_id, role)`. FK references to `places` and `parties` as comments only.
- `assets` — composite PK `(tenant_id, asset_id)`. `linked_place` as cross-DB-documentation-only FK to `places(place_id)`.
- `asset_owners` — composite PK `(tenant_id, asset_id, party_id)`. FK references documentation-only.
- `accounts` — composite PK `(tenant_id, account_id)`. Observer the `login_vault_ref` constraint: never a raw credential. Enforce with a SQLite CHECK: `CHECK (login_vault_ref IS NULL OR login_vault_ref LIKE 'op://%' OR login_vault_ref LIKE '1password://%' OR login_vault_ref LIKE 'vault://%')`.

Enum CHECKs per CF-6: `places.kind`, `assets.kind`, `accounts.kind`, `accounts.status`.

File-header comment:
```
-- places_assets_accounts projection schema — three linked entity families.
--
-- Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §12.
--
-- Cross-DB FK references (linked_place → places; organization/party_id →
-- parties; linked_asset → assets) are documentation only; SQLite cannot
-- enforce FKs across separate projection DBs. Integrity preserved by
-- upstream pipelines per [§2.3].
--
-- [§12] login_vault_ref MUST be a vault pointer, never a raw credential.
-- The CHECK constraint catches accidental writes. Real defense is the
-- adapter that emits account.added.
```

#### `adminme/projections/places_assets_accounts/handlers.py`

Six handler functions, one per subscribed event type:

- `apply_place_added` — INSERT with ON CONFLICT DO UPDATE keyed on `(tenant_id, place_id)`.
- `apply_place_updated` — UPDATE only fields in `field_updates`, filtered against an `_UPDATABLE_PLACE_COLUMNS` allowlist.
- `apply_asset_added` — INSERT with ON CONFLICT DO UPDATE keyed on `(tenant_id, asset_id)`.
- `apply_asset_updated` — UPDATE only allowlisted fields.
- `apply_account_added` — INSERT with ON CONFLICT DO UPDATE keyed on `(tenant_id, account_id)`.
- `apply_account_updated` — UPDATE only allowlisted fields.

`place_associations` and `asset_owners` are NOT populated by these handlers — those associations come in via separate event types (`place_association.added`, `asset_ownership.added`) that will be added in a future prompt when they're actually needed. The tables exist with their schemas so 07b's xlsx doesn't have to migrate later.

#### `adminme/projections/places_assets_accounts/queries.py`

Seven functions. Every one has `# TODO(prompt-08): wrap with Session scope check`. Every takes `conn` and `tenant_id` as required kwarg.

- `get_place(conn, *, tenant_id, place_id) -> dict | None`
- `list_places(conn, *, tenant_id, kind: str | None = None) -> list[dict]`
- `get_asset(conn, *, tenant_id, asset_id) -> dict | None`
- `list_assets_for_place(conn, *, tenant_id, place_id) -> list[dict]` — assets with `linked_place = ?`
- `get_account(conn, *, tenant_id, account_id) -> dict | None`
- `list_accounts_by_kind(conn, *, tenant_id, kind: str) -> list[dict]`
- `accounts_renewing_before(conn, *, tenant_id, cutoff_iso: str) -> list[dict]` — `status='active' AND next_renewal IS NOT NULL AND next_renewal <= cutoff_iso ORDER BY next_renewal ASC`

#### `adminme/projections/places_assets_accounts/__init__.py`

Exports `PlacesAssetsAccountsProjection(Projection)` with `name='places_assets_accounts'`, `version=1`, `subscribes_to=['place.added', 'place.updated', 'asset.added', 'asset.updated', 'account.added', 'account.updated']`, dispatching via handlers.apply_event.

#### `adminme/projections/money/schema.sql`

One table per BUILD.md §3.9, multi-tenant:

```sql
CREATE TABLE IF NOT EXISTS money_flows (
    flow_id             TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    from_party          TEXT,                    -- cross-DB FK doc-only → parties
    to_party            TEXT,                    -- cross-DB FK doc-only → parties
    amount_minor        INTEGER NOT NULL,
    currency            TEXT NOT NULL,           -- ISO 4217
    occurred_at         TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('paid','received','owed','reimbursable')),
    category            TEXT,
    linked_artifact     TEXT,                    -- doc-only → artifacts
    linked_account      TEXT,                    -- doc-only → accounts
    linked_interaction  TEXT,                    -- doc-only → interactions
    notes               TEXT,
    source_adapter      TEXT NOT NULL,           -- 'plaid' | 'receipts_ocr' | 'manual' | ...
    is_manual           INTEGER NOT NULL DEFAULT 0,  -- 1 if manually_added
    added_by_party_id   TEXT,                    -- populated for manually_added
    deleted_at          TEXT,                    -- populated for manually_deleted (soft delete)
    owner_scope         TEXT NOT NULL,
    visibility_scope    TEXT NOT NULL,
    sensitivity         TEXT NOT NULL DEFAULT 'normal'
                        CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, flow_id)
);

CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_occurred
    ON money_flows(tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_category
    ON money_flows(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_account
    ON money_flows(tenant_id, linked_account);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_manual
    ON money_flows(tenant_id, is_manual);
```

Note: soft-delete via `deleted_at` for `manually_deleted`. This preserves rebuild correctness — a row still exists with `deleted_at` populated. Plaid-sourced rows are never deleted through this projection (the adapter-side Plaid sync handles reversal/correction); the only soft-delete path is manual.

File-header comment explains the above.

#### `adminme/projections/money/handlers.py`

Three handler functions:

- `apply_money_flow_recorded(envelope, conn)` — INSERT with `is_manual=0`, `deleted_at=NULL`, `added_by_party_id=NULL`. ON CONFLICT DO UPDATE keyed on `(tenant_id, flow_id)` — per [§2.3], handlers don't verify cross-DB FK; if `linked_account` doesn't exist in accounts, the row still lands.
- `apply_money_flow_manually_added(envelope, conn)` — INSERT with `is_manual=1`, `source_adapter='manual'`, `added_by_party_id` populated from payload. ON CONFLICT DO UPDATE keyed on `(tenant_id, flow_id)`.
- `apply_money_flow_manually_deleted(envelope, conn)` — UPDATE the row setting `deleted_at=<payload.deleted_at>`. Soft delete; row persists for rebuild correctness. If the row doesn't exist (deletion-before-addition event order), the UPDATE is a no-op — log at INFO.

#### `adminme/projections/money/queries.py`

Six functions with TODO(prompt-08) markers:

- `get_money_flow(conn, *, tenant_id, flow_id) -> dict | None`
- `flows_in_range(conn, *, tenant_id, start_iso: str, end_iso: str) -> list[dict]` — `occurred_at BETWEEN ? AND ? AND deleted_at IS NULL ORDER BY occurred_at DESC`.
- `flows_by_category(conn, *, tenant_id, category: str, since_iso: str | None = None) -> list[dict]` — when `since_iso` is None, all time.
- `flows_by_account(conn, *, tenant_id, account_id: str, since_iso: str | None = None) -> list[dict]`
- `category_totals(conn, *, tenant_id, since_iso: str) -> dict[str, int]` — `SELECT category, SUM(amount_minor) GROUP BY category`. Returns dict of category → total_minor. Exclude deleted rows and rows with `category IS NULL`.
- `manual_flows(conn, *, tenant_id) -> list[dict]` — `is_manual = 1 AND deleted_at IS NULL`. Used by 07b to avoid double-counting when it regenerates the Raw Data sheet.

#### `adminme/projections/money/__init__.py`

Exports `MoneyProjection(Projection)` with `name='money'`, `version=1`, `subscribes_to=['money_flow.recorded', 'money_flow.manually_added', 'money_flow.manually_deleted']`.

#### Tests — `tests/unit/test_projection_places_assets_accounts.py` (≥12 tests)

Use the prompt-06 fixture pattern. Cover:
- Apply place.added; row exists with correct kind, geo.
- Apply place.updated; only listed fields change.
- Apply asset.added with `linked_place=<existing place_id>`; row exists.
- Apply asset.updated; fields change.
- Apply account.added with `login_vault_ref='op://vault/account'`; row exists, vault_ref intact.
- Apply account.added with `login_vault_ref='password123'`; handler propagates the SQLite CHECK failure (assert `IntegrityError` raised). This is belt; the real defense is the adapter. But test it so we catch a broken adapter.
- Apply account.updated; fields change.
- Idempotency: apply place.added twice; one row.
- Rebuild correctness: ~30-event fixture across all 3 entity families, snapshot, rebuild, snapshot, byte-equal.
- Query: `list_places(kind='home')` filters correctly.
- Query: `accounts_renewing_before` returns only matching rows in next-renewal order.
- Multi-tenant isolation: same place_id in tenant-a and tenant-b, each query returns its own.
- Scope canary stub: `sensitivity='privileged'` envelope lands with correct sensitivity column.

#### Tests — `tests/unit/test_projection_money.py` (≥10 tests)

Cover:
- Apply money_flow.recorded; row exists with `is_manual=0`, `deleted_at IS NULL`.
- Apply money_flow.manually_added; row exists with `is_manual=1`, `added_by_party_id` set.
- Apply money_flow.manually_deleted; row exists with `deleted_at` populated.
- Deletion-before-addition ordering (shouldn't happen but handler must not crash): apply `manually_deleted` for a nonexistent flow_id; no row created, INFO log.
- Idempotency: apply recorded twice; one row.
- Rebuild correctness: 30-event fixture including add→manually_added→manually_deleted sequence; rebuild; byte-equal.
- Query: `flows_in_range` filters correctly, excludes deleted.
- Query: `category_totals` sums correctly, excludes deleted, excludes NULL category.
- Query: `manual_flows` returns only `is_manual=1 AND deleted_at IS NULL`.
- Multi-tenant isolation.
- Scope canary stub.

Register `PlacesAssetsAccountsProjection` and `MoneyProjection` in `scripts/demo_projections.py` (extend, do not replace).

**Verify commit 2:**

```bash
poetry run pytest tests/unit/test_projection_places_assets_accounts.py \
                 tests/unit/test_projection_money.py -v 2>&1 | tail -5
# Expected: ≥22 tests passing.

# Regression: all prior projection tests still pass
poetry run pytest tests/unit/test_projection_parties.py \
                 tests/unit/test_projection_interactions.py \
                 tests/unit/test_projection_artifacts.py \
                 tests/unit/test_projection_commitments.py \
                 tests/unit/test_projection_tasks.py \
                 tests/unit/test_projection_recurrences.py \
                 tests/unit/test_projection_calendars.py -q 2>&1 | tail -3

poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

git add adminme/projections/places_assets_accounts/ adminme/projections/money/ \
        tests/unit/test_projection_places_assets_accounts.py \
        tests/unit/test_projection_money.py \
        scripts/demo_projections.py
git commit -m "phase 07a-2: places_assets_accounts + money projections"
```

If any test fails, STOP and fix before commit 3.

### Commit 3 — vector_search projection

One projection module, four files. vector_search is different from the others because it uses `sqlite-vec`'s `vec0` virtual table — which requires extension loading at connection time.

#### Extension loading

The `ProjectionRunner._open_projection_db()` method in `adminme/projections/runner.py` opens connections with SQLCipher's PRAGMA key. For vector_search specifically, after the PRAGMA but before the schema executescript, the connection also needs:

```python
conn.enable_load_extension(True)
import sqlite_vec
sqlite_vec.load(conn)
conn.enable_load_extension(False)
```

**This requires a targeted modification to `ProjectionRunner._open_projection_db()`.** Add an optional method hook on `Projection`: `def on_connection_opened(self, conn)` that defaults to no-op in the base class. `VectorSearchProjection` overrides it to call the extension-load sequence. Runner calls `projection.on_connection_opened(conn)` after PRAGMA key, before schema executescript.

This is a minimal, additive change to the base class and runner. It does NOT change the contract for any existing projection.

#### `adminme/projections/vector_search/schema.sql`

```sql
-- vector_search projection schema.
--
-- Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6, §8, §13.8.
--
-- [§13.8] privileged content MUST NOT be embedded or stored in this index.
-- Handler filters at write time (belt); Session (prompt 08) enforces at
-- read time (braces).
-- [§8] AdministrateMe does not call embedding models directly. Vectors
-- in embedding.generated events are pre-computed by a daemon that calls
-- OpenClaw's embedding endpoint.
--
-- vec0 is a sqlite-vec virtual table; it requires the sqlite-vec
-- extension to be loaded at connection time (see runner hook).

CREATE VIRTUAL TABLE IF NOT EXISTS vector_index USING vec0(
    embedding_id  TEXT PRIMARY KEY,
    embedding     float[1536],   -- default dim; actual dim carried in sidecar
    linked_kind   TEXT,
    linked_id     TEXT,
    sensitivity   TEXT,
    owner_scope   TEXT,
    tenant_id     TEXT
);

-- Sidecar table for exact-match lookups + metadata. vec0 doesn't expose
-- per-row text columns for arbitrary query, so we keep auxiliary data
-- in a normal table keyed by embedding_id.
CREATE TABLE IF NOT EXISTS embeddings_meta (
    tenant_id             TEXT NOT NULL,
    embedding_id          TEXT NOT NULL,
    linked_kind           TEXT NOT NULL,
    linked_id             TEXT NOT NULL,
    embedding_dimensions  INTEGER NOT NULL,
    model_name            TEXT NOT NULL,
    sensitivity           TEXT NOT NULL,
    source_text_sha256    TEXT NOT NULL,
    created_at_ms         INTEGER NOT NULL,
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, embedding_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_tenant_linked
    ON embeddings_meta(tenant_id, linked_kind, linked_id);
```

Note the `float[1536]` default is SQLite-vec convention; actual dims are recorded in `embeddings_meta.embedding_dimensions` so a future multi-model setup works (we insert whatever dim vec0 was compiled for; the sidecar tells us what model). For 07a, all test fixtures use 1536-dim.

#### `adminme/projections/vector_search/handlers.py`

One handler function:

- `apply_embedding_generated(envelope, conn)`:
  1. **Sensitivity filter at write time.** If `envelope.sensitivity == 'privileged'` OR `envelope.payload.sensitivity == 'privileged'`, log at INFO ("privileged embedding skipped per [§13.8]") and return without writing. Belt-and-braces: per [§6.3], a privileged event should never emit an `embedding.generated` at all (the daemon filters at read time), but we enforce again here.
  2. Validate embedding length matches `embedding_dimensions`; if mismatch, log WARN and return (upstream daemon bug).
  3. INSERT into `vector_index`: `INSERT OR REPLACE INTO vector_index (embedding_id, embedding, linked_kind, linked_id, sensitivity, owner_scope, tenant_id) VALUES (...)`. `vec0` tables don't support ON CONFLICT DO UPDATE syntax — `INSERT OR REPLACE` is the correct idempotency idiom.
  4. INSERT into `embeddings_meta` with ON CONFLICT DO UPDATE keyed on `(tenant_id, embedding_id)`.

Embeddings are passed as Python list[float] in the payload. vec0 expects raw bytes — use `sqlite_vec.serialize_float32(embedding_list)` to convert. This means `sqlite_vec` is imported at handler level (top of handlers.py). Both `sqlite-vec = "*"` and the serialization helper come from the same package.

#### `adminme/projections/vector_search/queries.py`

Four functions:

- `get_embedding_meta(conn, *, tenant_id, embedding_id) -> dict | None` — reads from `embeddings_meta` only, not vec0.
- `nearest(conn, *, tenant_id, query_vector: list[float], k: int = 10, exclude_sensitivity: str = 'privileged') -> list[dict]` — uses vec0's `MATCH` operator. Returns `[{'embedding_id', 'linked_kind', 'linked_id', 'distance'}, ...]`. Excludes rows where `sensitivity = exclude_sensitivity` via WHERE clause. Joins against `embeddings_meta` for linked_kind/linked_id.
- `embeddings_for_link(conn, *, tenant_id, linked_kind: str, linked_id: str) -> dict | None` — finds embedding for a specific interaction/artifact. Returns meta row.
- `count_embeddings(conn, *, tenant_id) -> int` — for smoke/status.

Every function has `# TODO(prompt-08): wrap with Session scope check`.

The `nearest` query is the one the inbox/search surfaces will use. It has multiple scope considerations that prompt 08 extends:
- Current implementation filters only `sensitivity != 'privileged'`. Prompt 08 will add `visibility_scope IN (current_session.allowed_scopes)` and `owner_scope` filters.

#### `adminme/projections/vector_search/__init__.py`

```python
class VectorSearchProjection(Projection):
    name = "vector_search"
    version = 1
    subscribes_to = ["embedding.generated"]
    schema_path = Path(__file__).parent / "schema.sql"

    def on_connection_opened(self, conn: Any) -> None:
        """Load the sqlite-vec extension. Required before schema.sql runs."""
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)
```

#### Runner modification

In `adminme/projections/runner.py`, `_open_projection_db()`, after the PRAGMA key and PRAGMA journal_mode lines, before the schema executescript, call:

```python
projection.on_connection_opened(conn)
```

Add the default implementation in `adminme/projections/base.py`:

```python
def on_connection_opened(self, conn: Any) -> None:
    """Optional hook called once per connection, after PRAGMA key is set
    and before schema.sql is executed. Default: no-op. Override for
    projections that need extension loading (vec0) or PRAGMAs beyond the
    defaults."""
    return None
```

#### Tests — `tests/unit/test_projection_vector_search.py` (≥10 tests)

Fixture: use a deterministic fake embedding. Helper function:

```python
def _fake_embedding(text: str, dim: int = 1536) -> list[float]:
    """Deterministic 1536-dim unit vector derived from sha256(text).
    Not a real embedding — just gives tests something that vec0 accepts
    and that nearest-neighbor queries can order deterministically."""
    import hashlib, struct
    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Expand 32 bytes to 1536 floats via deterministic repetition.
    floats = []
    for i in range(dim):
        byte_val = h[i % 32]
        floats.append((byte_val - 127.5) / 127.5)
    # Normalize.
    mag = sum(f * f for f in floats) ** 0.5
    return [f / mag for f in floats]
```

Tests:
- Apply `embedding.generated` with sensitivity='normal'; both `vector_index` and `embeddings_meta` have the row.
- Apply `embedding.generated` with envelope.sensitivity='privileged'; INFO log, NO rows in either table. (Use CF-5 discipline: follow with an innocuous `.generated` event to drive the checkpoint forward, then assert the privileged's absence.)
- Apply `embedding.generated` with payload.sensitivity='privileged' (envelope normal but payload privileged — defense in depth); same — NO rows.
- Idempotency: apply same `embedding.generated` twice; one row in vector_index, one in embeddings_meta.
- `nearest`: 5 known embeddings, query with a vector close to embedding #3, assert #3 is rank 1. (This is why we use the deterministic fake — near-ness is predictable.)
- `nearest` excludes privileged by default.
- `embeddings_for_link`: find meta by `(linked_kind='interaction', linked_id='int-1')`.
- Rebuild: 20 embeddings + 3 privileged skipped → rebuild → identical state (20 rows in vector_index, 20 in meta).
- Multi-tenant isolation: same embedding_id in tenant-a and tenant-b → each tenant's queries return only their own.
- Embedding-dimension validation: apply event with embedding of length 10 but `embedding_dimensions=1536`; WARN log, row NOT inserted.

**Verify commit 3:**

```bash
poetry run pytest tests/unit/test_projection_vector_search.py -v 2>&1 | tail -5
# Expected: ≥10 tests passing.

poetry run pytest tests/unit/test_projection_*.py -q 2>&1 | tail -3
# Expected: all 05/06/07a projection tests green.

poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

# Register VectorSearchProjection in scripts/demo_projections.py
# (add to the register() block; add 3 embeddings to the demo flow;
# query nearest in the status printout)

git add adminme/projections/vector_search/ adminme/projections/base.py \
        adminme/projections/runner.py tests/unit/test_projection_vector_search.py \
        scripts/demo_projections.py
git commit -m "phase 07a-3: vector_search projection + runner connection hook"
```

If any test fails, STOP and fix before commit 4.

### Commit 4 — integration + smoke + verification + BUILD_LOG + push

#### Extend `tests/integration/test_projection_rebuild.py`

Prompt 06 grew the integration fixture to ~800 events across 7 projections. 07a extends to ~1200 events across 10 projections:

- Add 50 places (40 `place.added` + 10 `place.updated`)
- Add 100 assets (80 `asset.added` + 20 `asset.updated`)
- Add 60 accounts (50 `account.added` + 10 `account.updated`)
- Add 150 money flows (100 `money_flow.recorded` + 30 `money_flow.manually_added` + 20 `money_flow.manually_deleted`)
- Add 40 embeddings (40 `embedding.generated`, 5 of which are privileged and should NOT land)

Assertions:
1. All 10 projections rebuild equivalent pre/post.
2. Sanity counts: 40 places, 80 assets, 50 accounts, 130 money_flows (100 recorded + 30 manually_added; manually_deleted soft-deletes don't remove rows, so all 130 are present with 20 having `deleted_at` populated), 35 vector_index rows (40 - 5 privileged skipped).
3. Cross-DB FK audit (informational): log orphan counts for `money_flows.linked_account → accounts`, `assets.linked_place → places`, `accounts.organization → parties`, `accounts.linked_asset → assets`.

#### Extend `scripts/demo_projections.py`

Add to the demo flow:
- 2 places (one home, one office), 2 assets (one vehicle linked to home, one appliance), 2 accounts (one utility linked to home, one bank).
- 4 money flows (one paid to each account, one reimbursable, one manually added).
- 3 embeddings (for interaction-1, artifact-0, party-m1; all normal sensitivity).

Add to the status printout:
- Place count, asset count, account count, active-accounts-renewing-in-30-days.
- Money flows total + sum by category.
- Vector index row count + 1 nearest-neighbor query result.

Demo must exit 0 in under 15 seconds on lab hardware.

#### Run full verification block

```bash
# Lint + types
poetry run ruff check adminme/ tests/ scripts/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

# Prompt 03-06 tests still pass
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py \
                 tests/unit/test_schema_registry.py tests/unit/test_event_validation.py \
                 tests/unit/test_projection_parties.py tests/unit/test_projection_interactions.py \
                 tests/unit/test_projection_artifacts.py tests/unit/test_projection_commitments.py \
                 tests/unit/test_projection_tasks.py tests/unit/test_projection_recurrences.py \
                 tests/unit/test_projection_calendars.py -q 2>&1 | tail -3
# Expected: all pass, 0 failed.

# Prompt 07a unit tests
poetry run pytest tests/unit/test_projection_places_assets_accounts.py \
                 tests/unit/test_projection_money.py \
                 tests/unit/test_projection_vector_search.py -q 2>&1 | tail -3
# Expected: ≥32 tests passing.

# Integration tests
poetry run pytest tests/integration/ -v 2>&1 | tail -5

# Canaries
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -3
poetry run pytest tests/unit/test_no_hardcoded_identity.py -v 2>&1 | tail -3
# Expected: instance-path PASSING, identity SKIPPED.

# Full suite
poetry run pytest -q 2>&1 | tail -3

# Inviolable-invariant greps
grep -iE "^anthropic|^openai|^sentence_transformers|anthropic =|openai =|sentence-transformers =" pyproject.toml \
    && echo "VIOLATION of [§8]" || echo "OK: no LLM/embedding SDKs in pyproject"

grep -rn "import anthropic\|import openai\|from anthropic\|from openai\|import sentence_transformers" adminme/ \
    && echo "VIOLATION of [§8]" || echo "OK: no LLM/embedding SDK imports in adminme/"

grep -rn "~/.adminme\|'/.adminme\|\"/.adminme" adminme/ bootstrap/ packs/ --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v "^docs/" || echo "OK: no hardcoded instance paths"

grep -rn "INSERT INTO.*projection\|projection_db.*write\|from adminme.projections.*import.*handlers" adminme/pipelines/ 2>/dev/null \
    || echo "OK: no pipeline→projection writes (no pipelines yet anyway)"

grep -rniE "james|laura|charlie|stice|morningside" adminme/ --include='*.py' \
    | grep -v "tests/\|# example\|# illustration" || echo "OK: no tenant identity in platform code"

# Smoke
poetry run python scripts/demo_projections.py 2>&1 | tail -30
```

Expected:
- Ruff: clean
- Mypy: clean (88+ source files — vector_search + places_assets_accounts + money + ops.py adds ~10 more)
- Prompt 03–06 tests: ~90+ passed.
- Prompt 07a unit tests: ≥32 passed.
- Integration: 1 rebuild test for 10 projections + 3 scope canary tests, all pass.
- Canaries: PASSING + SKIPPED as expected.
- Full suite: ~170+ passed, 1 skipped.
- All greps: OK.
- Smoke: clean, 10 projection row counts reported.

#### BUILD_LOG append

Append to `docs/build_log.md` under the existing entries. Template:

```markdown
### Prompt 07a — ops spine projections (places_assets_accounts, money, vector_search)
- **Refactored**: by Partner in Claude Chat, <refactor date>. Prompt file: prompts/07a-projections-ops-spine.md (~600 lines, quality bar = 06).
- **Session merged**: PR #<N>, commits <sha1>/<sha2>/<sha3>/<sha4>, merged <merge date>.
- **Outcome**: <MERGED or otherwise>.
- **Evidence**:
  - 3 projections: places_assets_accounts (3 entity tables + 2 association tables), money (1 table with is_manual + soft-delete), vector_search (vec0 virtual table + embeddings_meta sidecar).
  - 10 new event types registered at v1 per [D7] (place/asset/account × added/updated, money_flow × 3, embedding.generated).
  - ~32 new unit tests + integration rebuild extended to 10 projections + ~1200 events.
  - Runner gained `on_connection_opened` hook for projection-specific extension loading (vec0).
  - Privileged-filter at handler time on vector_search per [§13.8].
  - CHECK constraints consistent on enum columns per CF-6.
  - BUILD_LOG updated as part of Commit 4 per new rule.
  - Ruff clean, mypy clean, all inviolable greps OK.
- **Carry-forward for prompt 07b (xlsx forward daemon)**:
  - Forward daemon reads from all 10 projections' query functions. Query signatures stable.
  - `money.manual_flows` + `money_flow.manually_added` event type are already wired for 07c's reverse path.
  - The 07b forward daemon subscribes to event types but MUST NOT emit — it's a projection per [§2.2].
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - `money_flow.manually_added` and `money_flow.manually_deleted` events are registered; 07c emits these when principals edit the Raw Data sheet.
  - `task.updated`, `task.deleted`, `commitment.edited` (already registered in 06) cover the Tasks and Commitments sheets' reverse path.
  - xlsx reverse is an adapter not a projection — it emits.
- **Carry-forward for prompt 08**:
  - 3 new projections × ~6 queries = ~18 more TODO(prompt-08) markers across queries.py files. Total now ~38 across 10 projections.
- **Carry-forward for future embedding daemon**:
  - `embedding.generated` schema requires pre-computed vector in payload. AdministrateMe does not import embedding SDKs. Daemon will call OpenClaw's embedding endpoint per [§8].
```

Do NOT sidecar this commit. It's part of phase 07a-4.

If any failure appears, fix BEFORE commit 4. Do not commit a broken state.

```bash
git add tests/integration/test_projection_rebuild.py scripts/demo_projections.py docs/build_log.md
git commit -m "phase 07a-4: integration + smoke + verification + BUILD_LOG"
```

### Push + open PR

```bash
git log --oneline | head -6
# Expect 4 phase 07a-N commits on top of main

git status
# Expect clean working tree

git push origin HEAD
```

Try gh CLI first:

```bash
gh pr create \
  --base main \
  --head $(git branch --show-current) \
  --title "Phase 07a: ops spine projections (places_assets_accounts, money, vector_search)" \
  --body "$(cat <<'EOF'
Three L3 projections on top of prompt 06's scaffold, closing out the sqlite-backed projection work before xlsx_workbooks.

**Landed:**
- 10 new event types at v1 (place × 2, asset × 2, account × 2, money_flow × 3, embedding.generated)
- `places_assets_accounts` projection — 6 event subscriptions, 7 query functions, 5 tables (places, place_associations, assets, asset_owners, accounts)
- `money` projection — 3 event subscriptions, 6 query functions, soft-delete pattern for manually_deleted rows
- `vector_search` projection — 1 event subscription, 4 query functions, sqlite-vec `vec0` virtual table + `embeddings_meta` sidecar
- `Projection.on_connection_opened` hook added to base class for vec0 extension loading (backward-compatible, default no-op)
- Integration rebuild test extended to 10 projections, ~1200 events
- Demo script exercises all 10 projections

**Invariants respected:**
- [§1.1], [§2.2]: projections consume only, never emit
- [§6.3], [§13.8]: privileged content filtered at vector_search handler write-time (belt); Session (prompt 08) enforces at read-time (braces)
- [§8]: no embedding SDK imports in adminme/. Vectors come pre-computed in payload from the future embedding daemon
- [§12]: every query has explicit tenant_id kwarg
- [§15]/[D15]: every projection DB path routes through InstanceConfig
- [D7]: all new event types at v1

**Carry-forward for 07b (xlsx forward):**
- Query signatures across all 10 projections are stable. 07b's forward daemon reads them directly per [D4].
- `money.manual_flows` exists so 07b can distinguish is_manual rows when regenerating the Raw Data sheet.

**Carry-forward for 07c (xlsx reverse):**
- `money_flow.manually_added`/`manually_deleted` are registered; 07c emits these.
- Task/commitment reverse events use types registered in 06.

**Carry-forward for prompt 08:** 38 TODO(prompt-08) markers across 10 projection query files. Session wrapping is mechanical.

Single-purpose PR per phase-07a prompt. No sidecar fixes.
EOF
)"
```

If `gh pr create` returns `command not found` or a GitHub API permission error, fall back to MCP:

```
Call mcp__github__create_pull_request with:
  owner=junglecrunch1212
  repo=administrate-me-now
  base=main
  head=<your branch name>
  title="Phase 07a: ops spine projections (places_assets_accounts, money, vector_search)"
  body=<the same body text as above; note the MCP tool takes a literal string, no heredoc>
```

If the MCP tool also fails: report the exact error and stop. Do not retry with modified flags. James will decide next step.

---

## Stop

**Explicit stop message to the operator.** After the PR is opened, the MCP tool will display a webhook subscription message ("You are now subscribed to PR activity..."). This is informational. Do ONE round of post-PR status check:

```
Call mcp__github__pull_request_read with method=get_status, owner=junglecrunch1212, repo=administrate-me-now, pullNumber=<N>
Call mcp__github__pull_request_read with method=get_reviews, owner=..., pullNumber=<N>
Call mcp__github__pull_request_read with method=get_comments, owner=..., pullNumber=<N>
```

Report whatever is returned (typically: pending/empty/empty because no CI is configured on this repo). Then **stop**. Do NOT poll again. Do NOT wait for webhooks. Do NOT respond to review comments that arrive after the stop message.

**Stop template:**

```
Three ops spine projections in. Event log → projections path covers 10 of 11.
Branch: <harness-assigned branch name>
PR: <URL from gh pr create or MCP>
Commits: phase 07a-1 through phase 07a-4 on top of main.
Verification summary:
- ruff / mypy: clean
- prompt 03-06 tests: <N passed, 0 failed>
- prompt 07a unit tests: places_assets_accounts <N>, money <N>, vector_search <N> (total ≥32)
- integration rebuild: 10 projections' rebuild equivalence verified; cross-DB FK audit logged
- scope canary stubs: 3 new (prompt 08 extends)
- instance-path canary: PASSING
- identity canary: still skipped (prompt 08 or 17)
- full suite: <N> passed, 1 skipped
- inviolable-invariant greps: all OK
- smoke script: clean, 10 projection row counts reported
10 new event types at v1:
- place.{added,updated}
- asset.{added,updated}
- account.{added,updated}
- money_flow.{recorded,manually_added,manually_deleted}
- embedding.generated
Runner gained on_connection_opened hook — vec0 extension loads cleanly,
backward compatible with all prior projections (default no-op).
vector_search enforces [§13.8] at write time: privileged events produce
an INFO log and no row. Integration test exercises this with 5/40 privileged.
BUILD_LOG appended in Commit 4 per new Partner rule.
Post-PR status check: <CI result>, <reviews result>, <comments result>
Ready for prompt 07b (xlsx_workbooks forward daemon) once this branch is
reviewed and merged.
```

Then STOP. Do not merge the PR yourself. Do not push to main. Do not proceed to 07b.
