# Prompt 11c-i: L1-bridge daemon + central :3337 ingest + bridge.enrolled@v1

**Phase:** L1-bridge / Cohort D — first prompt to land bridge-side infrastructure per memo 04 / [D17] / [§6.19] / [PM-29] / BUILD.md §MEMBER BRIDGES. Per `prompts/PROMPT_SEQUENCE.md` row 117. Unlocks 11b-ii (Apple Calendar dual + Apple Reminders dual-deployment), 11c-ii (Cat-D knowledge adapters), 11d (member_knowledge projection), 11e Apple Contacts bridge half, and bootstrap wizard §10 (bridge enrollment).

**Depends on:** Cohort B closed (5 ABCs); Cohort C closed; canonical 11a/11b-i/11b-iii/11b-iv/11f all on main; memo 04 + [D17] on main.

**Estimated duration:** ~3-4 hours. Single prompt per PROMPT_SEQUENCE.md row 117 (NOT split per the sequence's tier disposition).

**Stop condition:** NEW `bridge/adminme_bridge/` Python package (separate Poetry project) shipping: bridge daemon entrypoint + `KnowledgeAdapter` Protocol + `BridgeContext` + ingest HTTP client + config loader + connector-pack loader scaffolding. NEW central `:3337 bridge` FastAPI service at `adminme/products/bridge/` exposing `/api/bridge/{ingest,ingest/batch,health}` per memo 04 §2.2 — Tailscale-User-Login authentication binds resolved member_id to envelope.owner_scope; mismatched claims hard-refuse with 403 per memo 04 §7(d) invariant. NEW `bridge.enrolled@v1` event schema at `adminme/events/schemas/bridge.py`. `verify_invariants.sh` exits 0.

---

## Read first

1. `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` — full memo. §2 (architecture), §3.2 (event schemas), §3.6 (bridge daemon shape), §3.7 (bootstrap), §7 (risk register).
2. `docs/DECISIONS.md` [D17] (personal knowledge ingestion is L1-bridge-shaped) — constitutional ground.
3. `docs/SYSTEM_INVARIANTS.md` §6.19 (bridge sovereignty) — invariant.
4. `ADMINISTRATEME_BUILD.md` §MEMBER BRIDGES + §L5-continued (bridge product) — spec.
5. `adminme/events/registry.py` (on main) — registration API.
6. `adminme/events/envelope.py` (on main) — `EventEnvelope` shape.
7. `adminme/lib/session.py` (on main) — Tailscale-identity-binding pattern (CONSOLE_PATTERNS §1). The bridge endpoint reuses this for Tailscale-User-Login resolution.
8. `adminme/daemons/adapter_supervisor.py` (on main) — daemon process pattern; quality-bar reference for the bridge daemon's asyncio supervisor.
9. `adminme/events/schemas/ingest.py` + `adminme/events/schemas/lists.py` (on main) — schema-module structure precedent for the new `bridge.py`.
10. `pyproject.toml` (on main) — root Poetry config; bridge subpackage uses a sibling layout.

## Operating context

11c-i ships **L1-bridge INFRASTRUCTURE only**. NO knowledge-source adapters (those are 11c-ii's Apple Notes / Voice Notes / Obsidian). NO member_knowledge projection (11d). NO bootstrap §10 wizard wiring (16-§10).

Three load-bearing items:

1. **`bridge/adminme_bridge/` package** — separate Poetry project (sibling to `adminme/`), per memo 04 §3.6 + [PM-29]. Layout:
   - `pyproject.toml` (NEW, separate Poetry; depends on `adminme` as editable install per UT-15 disposition below)
   - `adminme_bridge/__init__.py`
   - `adminme_bridge/main.py` (asyncio entrypoint; runs the adapter supervisor)
   - `adminme_bridge/config.py` (`~/.adminme-bridge/config.yaml` schema + loader)
   - `adminme_bridge/ingest_client.py` (HTTP POST to central `:3337 bridge` endpoint; retries; cold-start batch path)
   - `adminme_bridge/adapters/__init__.py`
   - `adminme_bridge/adapters/base.py` (NEW `KnowledgeAdapter` Protocol + `BridgeContext` dataclass — 11c-ii's adapters subclass this)
   - `adminme_bridge/packs/__init__.py` (connector-pack loader scaffolding; mirrors `adminme/lib/pack_registry/` pattern but for bridge-side packs filtered by `subkind: knowledge-source` per memo 04 §3.1)

2. **Central `:3337 bridge` FastAPI service** at `adminme/products/bridge/` — fifth Python product (predates 13a/13b's product framework but ships standalone per memo 04 §2.2). Layout:
   - `adminme/products/__init__.py` (if not present)
   - `adminme/products/bridge/__init__.py`
   - `adminme/products/bridge/app.py` (FastAPI app + lifespan)
   - `adminme/products/bridge/routers/__init__.py`
   - `adminme/products/bridge/routers/ingest.py` (`POST /api/bridge/ingest` + `POST /api/bridge/ingest/batch` + `GET /api/bridge/health`)
   - `adminme/products/bridge/auth.py` (Tailscale-User-Login header resolution → member_id via `party_tailscale_binding` projection per memo 04 §2.2; hard-refuse 403 on missing/mismatched per memo 04 §7(d))

3. **`bridge.enrolled@v1` event schema** at `adminme/events/schemas/bridge.py` (NEW) registering one event:
   - `BridgeEnrolledV1`: `{member_id: str, bridge_node_id: str, tailscale_identity: str, adapters_active: list[str], enrolled_at: datetime}` with `extra="forbid"`.
   - Registered via `registry.register("bridge.enrolled", 1, BridgeEnrolledV1)`.

OUT OF SCOPE per memo 04 + sequence row 117:

- **Knowledge-source adapters (Apple Notes / Voice Notes / Obsidian)** — 11c-ii ships these.
- **`note.*` and `voice_note.*` event schemas** — 11c-ii registers these.
- **`member_knowledge` projection** — 11d ships this.
- **Apple Contacts bridge-half + Apple Calendar bridge-half** — 11e + 11b-ii ship these.
- **`party_tailscale_binding` projection extension** — assumed to be a future addition (the bridge auth path uses it as a contract; implementation deferred unless already on main — check at orientation).
- **Bootstrap wizard §10 bridge enrollment** — 16-§10 ships this.
- **`launchd` plist generation** — operator runs the bridge daemon manually in Phase A; `launchd` config ships in 16-§10.
- **TLS termination via `tailscale serve`** — operator-side configuration documented in BUILD.md but not implemented in code here.
- **Cross-adapter changes** — Gmail / Plaid / Apple Reminders / Google Calendar / Google Tasks / lists projection preserved byte-for-byte.

Citations: [D17] (L1-bridge canonical), [§6.19] (bridge sovereignty — bridge runs only its assigned member's adapters), [§7.3, D17 corollary 1] (adapters never write projections), [§8.2, D6] (no LLM SDK in bridge code either), [D7] (new v1 schema additive), [D8] (correlation_id), [PM-29] (knowledge-source adapters at `bridge/` not `adminme/`), [PM-34] (the ABC count is unchanged; bridge `KnowledgeAdapter` is a NEW Protocol distinct from the central Cat-A through Cat-E ABCs because bridge-side adapters have a different ingest path — emit-to-HTTP-client rather than emit-to-event-log). Memo 04 §2, §3, §7. BUILD.md §MEMBER BRIDGES.

[VISION §"Real family / privacy"]: 11c-i is the load-bearing infrastructure for the per-member-bridge architecture. Each member's iCloud account lives only on their own bridge; central CoS Mac Mini never signs into a member's iCloud. Identity-first privacy enforced at the physical layer.

**UT-15 disposition (event-schema sharing):** Bridge package depends on `adminme` as an **editable install** (`pip install -e ../adminme`) per memo 04 §3.6 recommendation. Pro: schemas stay in sync; bridge imports `adminme.events.schemas.bridge.BridgeEnrolledV1` directly. Con: bridge's `pyproject.toml` carries an editable-install reference that's awkward on a fresh bridge Mac Mini without the repo cloned. Resolution at 11c-i refactor time: ship the editable-install path; the bridge bootstrap mini-wizard (16-§10) handles the install order. Vendored-copy fallback documented as a future split if cross-version drift becomes painful.

## Objective

Build the L1-bridge infrastructure: bridge daemon package shell at `bridge/adminme_bridge/`; central `:3337 bridge` FastAPI service at `adminme/products/bridge/`; `bridge.enrolled@v1` schema. NO knowledge-source adapters (11c-ii). NO bootstrap-wizard wiring (16-§10).

## Out of scope

(See above.)

## Approach (architectural sketch)

Single-prompt scope per PROMPT_SEQUENCE.md row 117. Four-commit structure:

- **c1**: Schema registration — `adminme/events/schemas/bridge.py` (NEW, ~30 lines) + registry call. Verifies `registry.get("bridge.enrolled", 1)` post-commit.

- **c2**: Central `:3337 bridge` FastAPI service at `adminme/products/bridge/` (~250 lines across 4-5 files):
  - `app.py` — FastAPI app + lifespan + uvicorn config (bind 127.0.0.1:3337 — `tailscale serve` is operator-side).
  - `auth.py` — `Tailscale-User-Login` header → member_id resolver (with hard-refuse 403 on missing/mismatched per memo 04 §7(d)).
  - `routers/ingest.py` — three endpoints. `/api/bridge/ingest` validates envelope shape, resolves member from Tailscale identity, **stamps `owner_scope = private:<member_id>`** (REGARDLESS of claimed value; mismatched claim → 403 per memo 04 §2.2), appends to event log via `EventStore`. `/api/bridge/ingest/batch` same with batched envelopes + per-event validation (one bad event refuses the whole batch with 400 + offset). `/api/bridge/health` GET returns liveness.

- **c3**: Bridge daemon package at `bridge/adminme_bridge/` (~300 lines across 7 files) + bridge `pyproject.toml`:
  - `pyproject.toml` — separate Poetry project, depends on `adminme` via path-based editable install (`tool.poetry.dependencies.adminme = {path = "..", develop = true}`).
  - `main.py` — asyncio entrypoint. Reads config; instantiates `BridgeContext`; runs the `BridgeSupervisor` (pack-loader-driven adapter list); awaits SIGTERM.
  - `config.py` — `~/.adminme-bridge/config.yaml` schema. Fields: `member_id`, `tailscale_identity`, `central_endpoint_url` (default `https://cos.<tailnet>/api/bridge/ingest`), `adapters` (list of enabled-adapter-pack ids — 11c-ii populates Apple Notes / Voice Notes / Obsidian as defaults).
  - `ingest_client.py` — HTTP client. POST envelope to central `:3337 bridge`; exponential backoff on 5xx; respects 429 retry-after; batch path for cold-start with chunked uploads.
  - `adapters/base.py` — `KnowledgeAdapter` Protocol (NEW — distinct from central Cat-A-E ABCs per memo 04 §3.1 / [PM-29]): `subkind: str`, `start(ctx: BridgeContext)`, `stop()`, `status() -> AdapterStatus`. Also `BridgeContext` dataclass: `member_id`, `ingest_client`, `correlation_id_factory`, `now_factory`.
  - `adapters/__init__.py` — exports.
  - `packs/__init__.py` — connector-pack loader scaffolding mirroring `adminme/lib/pack_registry/parser.py` shape; filters for `kind: adapter, subkind: knowledge-source`.

- **c4**: Tests (~25 across unit + integration) + BUILD_LOG:
  - `tests/unit/products/bridge/test_auth.py` — Tailscale-User-Login resolution + hard-refuse cases.
  - `tests/unit/products/bridge/test_ingest_router.py` — happy path; owner_scope stamping; mismatched-claim 403; batch path; one-bad-event-batch-refusal.
  - `bridge/tests/unit/test_main.py` (bridge package's own test dir) — daemon entrypoint smoke.
  - `bridge/tests/unit/test_ingest_client.py` — retry + backoff + batch chunking.
  - `bridge/tests/unit/test_adapters_base.py` — `KnowledgeAdapter` Protocol shape + `BridgeContext` fields.
  - `tests/integration/test_bridge_ingest_roundtrip.py` — synthetic event from `IngestClient` lands in the central event log via the `:3337` service in lab mode.
  - `tests/unit/events/schemas/test_bridge_schemas.py` — `BridgeEnrolledV1` field set + `extra="forbid"` + registry lookup.

## Deliverables

(All four c-bands per §"Approach" above. Test floor: ≥22 tests across the seven files listed.)

`docs/build_log.md` — append the 11c-i entry per the canonical 11a/11b-iv/11f c4 template; explicitly note "**Unlocks 11b-ii / 11c-ii / 11d / 11e Apple-half / 16-§10**".

## Verification

```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.bridge import BridgeEnrolledV1; assert registry.get('bridge.enrolled', 1) is BridgeEnrolledV1; print('bridge.enrolled@v1 verified ✓')"

poetry run pytest tests/unit/products/bridge/ tests/unit/events/schemas/test_bridge_schemas.py tests/integration/test_bridge_ingest_roundtrip.py -v

cd bridge && poetry run pytest tests/unit/ -v && cd ..

poetry run mypy adminme/products/bridge/ 2>&1 | tail -5
poetry run mypy bridge/adminme_bridge/ 2>&1 | tail -5  # bridge has its own mypy config inheriting from root

poetry run ruff check adminme/products/bridge/ bridge/adminme_bridge/ 2>&1 | tail -3

bash scripts/verify_invariants.sh
# Expected: exit 0 (no LLM SDK imports anywhere; no tenant identity hardcoded; bridge ingest binds owner_scope via Tailscale only)
```

## Stop

When `bridge/adminme_bridge/` daemon imports cleanly, central `:3337` FastAPI service routes `/api/bridge/{ingest,ingest/batch,health}` with Tailscale-identity-bound `owner_scope` stamping, `bridge.enrolled@v1` registers via the canonical registry, and the integration test demonstrates a synthetic event flowing through the round-trip, c4 is done. **Unlocks the bridge-dependent prompt cohort.** Do NOT proceed to 11c-ii (knowledge-source adapters) or 11b-ii dual-deployment without operator review.

## What this PR is NOT

Parent draft only. v4 refactor follows in `partner/refactor-11c-i`.

No constitutional doc edits. No knowledge-source adapter code. No bootstrap-wizard wiring.
