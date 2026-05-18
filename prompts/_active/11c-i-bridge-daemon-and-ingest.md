# Prompt 11c-i: L1-bridge daemon + central :3337 ingest + bridge.enrolled@v1

**Phase:** L1-bridge / Cohort D. Per memo 04 / [D17] / [§6.19] / [PM-29] / BUILD.md §MEMBER BRIDGES + PROMPT_SEQUENCE.md row 117. Parent draft: `prompts/11c-i-bridge-daemon-and-ingest.md` (merged PR #183).
**Branch:** `claude/11c-i-c<N>` per c-band.
**Stop condition:** NEW `bridge/adminme_bridge/` separate Poetry package (sibling to `adminme/`) + NEW central `:3337 bridge` FastAPI service at `adminme/products/bridge/` + NEW `bridge.enrolled@v1` schema. Tailscale-User-Login → `owner_scope` binding enforced; mismatched claims hard-refuse 403. `verify_invariants.sh` exits 0.
**Depends-on:** all prior Cohort D step 2 closed (11a / 11b-i / 11b-iii / 11b-iv / 11f all on main); memo 04 + [D17] + [§6.19] on main; parent draft merged (PR #183, main `7c4963c9`).
**Quality-bar reference:** `adminme/daemons/adapter_supervisor.py` (daemon pattern); canonical-build precedents 11b-iii / 11b-iv (fresh-pack structure); `prompts/_active/11f-lists-projection.md` (most-recent canonical-build c1-c4 shape).
**Estimated duration:** c1 ~20 min (schema) · c2 ~75 min (central service) · c3 ~90 min (bridge package) · c4 ~60 min (tests + BUILD_LOG) · total ~3.5-4 hrs.

---

## §1 Behavioral grounding

11c-i ships **L1-bridge INFRASTRUCTURE only** — daemon shell + central ingest + schema + Protocol. Three load-bearing items:

1. **`bridge/adminme_bridge/` package** at the repo root, sibling to `adminme/`, with its own `pyproject.toml` per [PM-29]. The daemon imports event schemas from `adminme.events.schemas.bridge` via editable install (UT-15 resolved per parent draft). Distinct from central Cat-A-E ABCs per [PM-34]: `KnowledgeAdapter` is a **Protocol** (not an ABC) because bridge adapters emit-to-HTTP-client, not emit-to-event-log.

2. **Central `:3337 bridge` FastAPI service** at `adminme/products/bridge/`. Predates 13a/13b product framework — ships as a standalone uvicorn-bindable FastAPI app on `127.0.0.1:3337` per memo 04 §2.2 (`tailscale serve` provides TLS termination operator-side). Three endpoints: `POST /api/bridge/ingest`, `POST /api/bridge/ingest/batch`, `GET /api/bridge/health`. **F-3 (HARD): Tailscale-User-Login binds owner_scope** — the ingest router REPLACES any claimed `owner_scope` on the incoming envelope with `private:<resolved_member_id>` from the Tailscale identity per memo 04 §2.2. A claim that doesn't match the resolved identity is a **hard 403 refusal**, NOT silently accepted, NOT silently overwritten. Logged to the privileged-access log as an attempted scope violation per memo 04 §7(d).

3. **`bridge.enrolled@v1` event schema** at `adminme/events/schemas/bridge.py` — `{member_id, bridge_node_id, tailscale_identity, adapters_active, enrolled_at}` with `extra="forbid"` per [D7]. Emitted by the bridge bootstrap mini-wizard (which ships in 16-§10) post-pairing. 11c-i registers the schema; emission lives downstream.

OUT OF SCOPE per parent draft §"Operating context":
- Knowledge-source adapters — 11c-ii.
- `note.*` / `voice_note.*` schemas — 11c-ii.
- `member_knowledge` projection — 11d.
- Apple Contacts / Apple Calendar bridge halves — 11e / 11b-ii.
- `party_tailscale_binding` projection extension — contract assumed; verify at orientation (if not on main, ship a minimal mock-able resolver as a future-injected dependency).
- Bootstrap §10 wizard — 16-§10.
- `launchd` plist — 16-§10.
- `tailscale serve` TLS — operator-side.
- Cross-adapter changes.

Citations: [D17] (L1-bridge canonical shape), [§6.19] (bridge sovereignty), [§7.3, D17 corollary 1] (adapters never write projections), [§8.2, D6] (no LLM SDK), [D7] (additive v1 schema), [D8] (causation/correlation), [PM-29] (`bridge/` not `adminme/`), [PM-34] (`KnowledgeAdapter` is Protocol; ABC count unchanged). Memo 04 §2, §3, §7. BUILD.md §MEMBER BRIDGES. [VISION §"Real family / privacy"]: identity-first privacy enforced at physical layer (per-member iCloud isolation on bridge devices).

## §2 Read first

**c1 (schema registration) — 3 files**

- `prompts/11c-i-bridge-daemon-and-ingest.md` (merged parent).
- `adminme/events/registry.py` — `registry.register(name, version, model)` API.
- `adminme/events/schemas/lists.py` (on main) — most-recent additive-v1-schema-module precedent.

**c2 (central :3337 service) — 6 files**

- `adminme/events/envelope.py` — `EventEnvelope` shape (what the ingest router unpacks + appends).
- `adminme/lib/session.py` — Tailscale-identity resolution pattern (CONSOLE_PATTERNS §1).
- `adminme/events/log.py` (or equivalent) — `EventLog` append API.
- `adminme/projections/parties/` — `party_tailscale_binding` projection (if present on main; otherwise document as injected dependency).
- `pyproject.toml` (on main) — FastAPI + uvicorn already in deps (used by other product scaffolding TODOs).
- `adminme/daemons/adapter_supervisor.py` — daemon pattern reference (long-lived process supervision).

**c3 (bridge package) — 5 files**

- `pyproject.toml` (on main) — root Poetry config; bridge subpackage uses sibling-Poetry-project pattern.
- `adminme/daemons/adapter_supervisor.py` — asyncio supervisor pattern for the bridge daemon's `BridgeSupervisor`.
- `adminme/lib/pack_registry/parser.py` — pack-manifest parsing precedent for connector-pack loader.
- `adminme/lib/adapters/__init__.py` — Protocol/ABC pattern precedent (the bridge's `KnowledgeAdapter` is a Protocol, not an ABC, but follows the same shape).
- `adminme/events/schemas/bridge.py` (on the c1 branch) — `BridgeEnrolledV1` import target for bridge package imports.

**c4 (tests + BUILD_LOG) — 4 files**

- `tests/unit/products/bridge/` (NEW directory; standard `tests/unit/<package>/` mirror of `adminme/products/bridge/`).
- `bridge/tests/unit/` (NEW directory; bridge package's OWN test tree per the sibling-Poetry pattern).
- `tests/integration/` — for the round-trip integration test.
- `docs/build_log.md` — append entry; explicitly note "**Unlocks 11b-ii / 11c-ii / 11d / 11e Apple-half / 16-§10**".

## §3 Reconciliation notes

- **F-1 (HARD/standing): READ FILE CONTENTS for schemas in c1.** `adminme/events/schemas/bridge.py` is NEW (no collision risk), but the c1 author MUST grep `registry.register("bridge.added"` etc. across `adminme/events/schemas/*.py` to confirm no shadow.
- **F-2 (HARD/from canonical 11a): MAP existing infrastructure; do not invent.** Tailscale-identity resolution mirrors `CONSOLE_PATTERNS.md §1` + `adminme/lib/session.py`. Reuse the existing `Tailscale-User-Login` header pattern.
- **F-3 (HARD/new for 11c-i): owner_scope binding is mandatory + non-overrideable per memo 04 §2.2.** The ingest router IGNORES the envelope's claimed `owner_scope` field; it sets `owner_scope = "private:" + resolved_member_id` from Tailscale identity. A claim that disagrees with the resolved identity hard-refuses 403. Pinned by c4 tests.
- **F-4 (HARD/standing): test basename uniqueness.** `test_bridge_schemas.py`, `test_auth.py`, `test_ingest_router.py`, `test_main.py` (bridge), `test_ingest_client.py`, `test_adapters_base.py` (bridge), `test_bridge_ingest_roundtrip.py` — all unique across the repo (verified — no `bridge` prefix exists yet).
- **F-5 (SOFT): per-file ceilings.** central service ≤250 lines total across all `adminme/products/bridge/` files. Bridge package ≤350 lines total across `bridge/adminme_bridge/`. Schema file ≤50 lines.
- **F-6 (HARD/from canonical 11a c4): NO empty `__init__.py` in pack-internal tests dirs.** This prompt's test dirs are framework-level (`tests/unit/products/bridge/`) + bridge-package-internal (`bridge/tests/unit/`) — both REQUIRE `__init__.py` markers. The F-6 rule about packs-only does not apply here.
- **F-7 (HARD/new for 11c-i): bridge package depends on `adminme` via path-based editable install.** `tool.poetry.dependencies.adminme = {path = "..", develop = true}` in `bridge/pyproject.toml`. Bootstrap mini-wizard (16-§10) handles install order on real bridge Mac Minis.
- **F-8 (SOFT/new): `party_tailscale_binding` resolution.** If the projection exists on main, the auth resolver queries it. If NOT on main, ship a minimal inject-able resolver Protocol that returns `member_id` from `tailscale_identity` — production wires the real projection; tests inject a fixture. Verify at orientation.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `EventEnvelope` import path** — `from adminme.events.envelope import EventEnvelope`. Verify exact field set; the ingest router constructs envelopes from incoming JSON.
- **A-2: `EventLog` append API** — `event_log.append(envelope)` or `event_log.append_sync(envelope)`. Verify async vs sync at orientation.
- **A-3: `Tailscale-User-Login` header parsing** — does an existing utility in `adminme/lib/session.py` extract member_id from this header? If yes, REUSE; if no, ship the resolver inline in `adminme/products/bridge/auth.py`.
- **A-4: `party_tailscale_binding` projection on main** — does it exist? Check `adminme/projections/`. If no, ship the injected-resolver Protocol per F-8.
- **A-5: FastAPI + uvicorn already in `pyproject.toml`** — used by the existing TODOs for 13a/13b products. Confirm at orientation; if missing, add to the root pyproject.
- **A-6: `adminme/products/__init__.py`** — does it exist? If yes, the new `adminme/products/bridge/` subdirectory is purely additive. If no, c2 creates the products top-level too.
- **A-7: bridge `Poetry` editable install path** — `path = ".."` works from `bridge/pyproject.toml` pointing at the root `pyproject.toml`. Verify Poetry version supports `develop = true`.

## §5 Objective

Build L1-bridge infrastructure: bridge daemon package (no knowledge adapters yet); central `:3337 bridge` ingest service with Tailscale-identity-bound `owner_scope` enforcement; `bridge.enrolled@v1` schema. Unlocks 11b-ii / 11c-ii / 11d / 11e Apple-half / 16-§10.

## §6 Out of scope

(See §1 above + parent draft §"Operating context".)

## §7 Deliverables

**c1: NEW `adminme/events/schemas/bridge.py` (~50 lines)** — `BridgeEnrolledV1(BaseModel)` with `model_config = {"extra": "forbid"}` + the field set per §1 item 3 + `registry.register("bridge.enrolled", 1, BridgeEnrolledV1)` at module-load. Plus module docstring citing memo 04 §3.2.

**c2: NEW `adminme/products/bridge/` service (~250 lines across 5 files)**:

1. `__init__.py` — empty package marker (or minimal export).
2. `app.py` — FastAPI app instantiation + lifespan + uvicorn entrypoint shape. Binds `127.0.0.1:3337`. Lifespan opens the `EventLog` connection.
3. `auth.py` — `resolve_tailscale_identity(request: Request) -> str` raises HTTP 403 on missing header; returns `tailscale_identity` string. `resolve_member_id(tailscale_identity: str, conn: <party_binding_conn>) -> str` resolves via `party_tailscale_binding` projection (or injected resolver per F-8); raises HTTP 403 on unknown identity.
4. `routers/__init__.py` + `routers/ingest.py`:
   - `POST /api/bridge/ingest` — request body validates envelope shape via Pydantic; resolves member; **F-3 owner_scope stamping** (refuse 403 on claim/resolved mismatch); appends to event log; returns 202 Accepted with envelope id.
   - `POST /api/bridge/ingest/batch` — accepts `{envelopes: [...]}`; same resolution + stamping per envelope; rejects whole batch with 400 + offset on first invalid envelope.
   - `GET /api/bridge/health` — returns `{status: "ok", bridge_endpoint: "/api/bridge/ingest", ingest_count: <atomic counter>}`.

If `adminme/products/__init__.py` doesn't exist, c2 creates it too.

**c3: NEW `bridge/adminme_bridge/` package (~350 lines across 7 files) + `bridge/pyproject.toml`**:

1. `bridge/pyproject.toml` — separate Poetry config; depends on `adminme` via path-editable; dev deps include `pytest` + `pytest-asyncio` + `httpx`.
2. `bridge/adminme_bridge/__init__.py` — minimal exports.
3. `bridge/adminme_bridge/main.py` — asyncio entrypoint. Reads `~/.adminme-bridge/config.yaml` via `config.load_config()`; instantiates `BridgeContext`; instantiates `BridgeSupervisor` (analog to `adapter_supervisor.py` but bridge-side); awaits SIGTERM; clean shutdown.
4. `bridge/adminme_bridge/config.py` — `BridgeConfig` pydantic model + `load_config(path: Path) -> BridgeConfig` reading YAML. Fields per parent draft §"Approach" c3.
5. `bridge/adminme_bridge/ingest_client.py` — `IngestClient(central_endpoint_url, tailscale_identity_header)` with `async def submit(envelope) -> SubmitResult` (POST `/api/bridge/ingest`) + `async def submit_batch(envelopes) -> SubmitBatchResult` (chunked). Exponential backoff on 5xx; respects 429 retry-after. Uses `httpx.AsyncClient`.
6. `bridge/adminme_bridge/adapters/__init__.py` — exports.
7. `bridge/adminme_bridge/adapters/base.py` — `KnowledgeAdapter` Protocol (NOT abstract; structural) with `subkind: ClassVar[str]`, `async start(self, *, ctx: BridgeContext) -> None`, `async stop(self) -> None`, `def status(self) -> AdapterStatus`. `BridgeContext` dataclass: `member_id`, `ingest_client`, `correlation_id_factory`, `now_factory`.
8. `bridge/adminme_bridge/packs/__init__.py` — connector-pack loader scaffolding mirroring `adminme/lib/pack_registry/parser.py` shape; filters for `kind: adapter, subkind: knowledge-source` per memo 04 §3.1.

**c4: Tests (≥22 floor) + BUILD_LOG**:

Framework-level (`tests/unit/products/bridge/`):
- `test_bridge_schemas.py` (~6 tests): `BridgeEnrolledV1` field set, `extra="forbid"` rejection, registry lookup.
- `test_auth.py` (~6 tests): missing-header 403, unknown-identity 403, valid identity returns member_id, header-name case-insensitive.
- `test_ingest_router.py` (~8 tests): happy-path single ingest, batch happy-path, **F-3 owner_scope stamping verified** (claim ignored, resolved-identity stamped), claim-mismatch hard-refuse 403, malformed envelope 422, batch with one bad envelope refuses whole batch 400 + offset, health endpoint shape, atomic-counter increment.

Bridge package internal (`bridge/tests/unit/`):
- `test_main.py` (~2 tests): daemon entrypoint smoke + SIGTERM handling.
- `test_ingest_client.py` (~5 tests): submit happy path, 5xx retry+backoff, 429 retry-after respected, batch chunking, batch failure reporting.
- `test_adapters_base.py` (~3 tests): `KnowledgeAdapter` Protocol structural-typing check (a sample class satisfies); `BridgeContext` dataclass shape.

Integration (`tests/integration/`):
- `test_bridge_ingest_roundtrip.py` (~3 tests): synthetic event from `IngestClient` → central `:3337` service → event log → readable; owner_scope correctly stamped; mismatched-claim case rejected end-to-end.

`docs/build_log.md` — 11c-i entry per the canonical c4 template. **Mark "Unlocks 11b-ii / 11c-ii / 11d / 11e Apple-half / 16-§10" prominently.** Carry-forward for 11c-ii: `KnowledgeAdapter` Protocol is now available at `bridge.adminme_bridge.adapters.base`; 11c-ii's Apple Notes / Voice Notes / Obsidian adapters subclass this Protocol.

## §8 Commit structure

Four commits. Format `phase 11c-i commit <N>: <imperative summary>`.

- **c1: `phase 11c-i commit 1: register bridge.enrolled@v1 schema`** — `adminme/events/schemas/bridge.py` (NEW, ~50 lines).
- **c2: `phase 11c-i commit 2: central :3337 bridge FastAPI ingest service`** — `adminme/products/bridge/` (NEW, ≤250 lines).
- **c3: `phase 11c-i commit 3: bridge daemon package + KnowledgeAdapter Protocol`** — `bridge/adminme_bridge/` (NEW, ≤350 lines) + `bridge/pyproject.toml`.
- **c4: `phase 11c-i commit 4: bridge ingest tests + roundtrip + BUILD_LOG`** — ≥22 tests + BUILD_LOG entry.

## §8.5 Branch strategy — STACKED

c-band file scopes mostly non-overlapping. The only shared file is `docs/build_log.md` (c4 only — append-only). c2 + c3 do NOT touch the same files (c2 = `adminme/products/bridge/`; c3 = `bridge/adminme_bridge/` + `bridge/pyproject.toml`). Per prior precedent (11r-c / 11a / 11b-i / 11b-iii / 11b-iv / 11f): zero ghost conflicts expected.

Test floor: ≥22 total across the seven test files.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.bridge import BridgeEnrolledV1; assert registry.get('bridge.enrolled', 1) is BridgeEnrolledV1; print('bridge.enrolled@v1 verified ✓')"
```

**c2**:
```bash
poetry run python -c "from adminme.products.bridge.app import app; from fastapi.testclient import TestClient; client = TestClient(app); r = client.get('/api/bridge/health'); assert r.status_code == 200; print('c2 health endpoint ✓')"
poetry run mypy adminme/products/bridge/ 2>&1 | tail -5
```

**c3**:
```bash
cd bridge && poetry install --no-root --without dev || poetry install --no-root  # ensure editable install resolves
cd bridge && poetry run python -c "from adminme_bridge.adapters.base import KnowledgeAdapter, BridgeContext; print('c3 KnowledgeAdapter Protocol + BridgeContext ✓')"
cd bridge && poetry run mypy adminme_bridge/ 2>&1 | tail -5
cd ..
```

**c4**:
```bash
poetry run pytest tests/unit/products/bridge/ tests/integration/test_bridge_ingest_roundtrip.py -v
cd bridge && poetry run pytest tests/unit/ -v && cd ..
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `bridge/adminme_bridge/` imports cleanly + `adminme/products/bridge/` FastAPI service routes correctly + `bridge.enrolled@v1` registers via the canonical registry + integration test demonstrates the round-trip + owner_scope stamping is enforced (claim ignored; resolved-identity stamped; mismatched-claim 403), c4 is done. **Unlocks the entire bridge-dependent cohort.** Do NOT proceed to 11c-ii or 11b-ii without operator review.

After merge: Partner runs three-job QC. Batched partner-state-snapshot PR via Chrome MCP `file_upload` covers post-11a + post-11b-i + post-11b-iii + post-11b-iv + post-11f + post-11c-i.
