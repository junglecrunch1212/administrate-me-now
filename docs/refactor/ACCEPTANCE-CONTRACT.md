# Release acceptance contract

Gate 3 status (2026-07-30): **AC-03 PASS** in the production composition with the deterministic Cat-A/OpenClaw fixture. The test is no longer an xfail. AC-04 remains the single explicit Phase 4 product-slice xfail.

## Rules

These are release gates, not aspirational checks. A gate passes only from a clean checkout on the stated supported platform with production composition, credential-free fixtures/fake services, retained machine-readable evidence, and no unexpected skip/xfail. Unit mocks cannot substitute for a process boundary named in a gate. Golden skeletons are marked strict xfail during refactor; the release command uses `--runxfail`, so missing capability fails loudly.

Standard environment: supported macOS lab, Python 3.11, Node 22 build tool, exact lockfiles, exact pinned OpenClaw release, loopback-only runtime, temporary encrypted instance directories, no real household account. Every command below must be wrapped by a versioned script/Make target in Phase 2; the current command is the intended interface.

## Gates

| ID | Executable release requirement | Required evidence / pass condition |
|---|---|---|
| AC-00 | `git diff --check && git status --porcelain` after build/test cleanup | Clean tracked tree; generated evidence stored only in declared artifact directory; no secret/unrelated file. |
| AC-01 | `./scripts/acceptance/clean-install.sh --fixture two-adult-one-child` | From a fresh macOS account/volume: locked install, all ten bootstrap sections, observation on, secret-reference scan clean, repeat run no-op, Doctor healthy. |
| AC-02 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_runtime_spine.py -q` | One Python process composes the encrypted log, durable bus, exact 13 projections, pipelines, adapters/xlsx, product namespaces, and bridge ingest; append commits before waking consumers; readiness, shutdown, and encrypted reopen are truthful. |
| AC-03 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_communication_commitment_vertical_slice.py -q` | **Gate 3 PASS:** credential-free inbound communication -> encrypted durable event -> bus -> Parties/Interactions -> schema-bound OpenClaw fixture provenance -> proposal-only review -> independent principal approval and observation-gated delivery intent -> API/xlsx -> complete/defer/snooze/block/unblock outcomes -> changed deterministic what-now. Duplicate requests, stale writes, wrong owners/roles, bad remote responses, replay, projection rebuild, crash-after-append, and restart preserve semantics. |
| AC-04 | `python -m pytest tests/acceptance/test_member_bridge_knowledge_vertical_slice.py --runxfail -q` | Enrolled member bridge -> owner-stamped knowledge event -> `member_knowledge` and allowed `vector_search`; another member/child/ambient/OpenClaw generic memory cannot read it; correction/replay proven. |
| AC-05 | `python -m pytest tests/acceptance/test_semantic_separation_and_feedback.py --runxfail -q` | Calendar/task/commitment/list/recurrence/scheduler schemas and stores remain distinct; Cat-B external edit round-trips; completion/defer/snooze/block/correction updates linked projections without type collapse. |
| AC-06 | `python -m pytest tests/acceptance/test_contextual_whatnow.py --runxfail -q` | Controlled clock/context fixtures prove different explainable results for now/hour/daypart/day/week/longer, energy/effort, availability, blockers, profile, scope, freshness, and lifecycle feedback. |
| AC-07 | `python -m pytest tests/acceptance/test_encryption_and_all_projections.py --runxfail -q` | Raw DB lacks plaintext sentinel and rejects wrong key; exact 13-name registry; each projection consumes a canonical event; full replay is semantically/byte equivalent as defined; no privileged vector row. |
| AC-08 | `python -m pytest tests/acceptance/test_member_child_authorization.py --runxfail -q` | `authMember`/`viewMember`, owner/visibility/sensitivity, trusted-proxy identity, privileged audit, calendar busy redaction, child direct-route denial, narrow portal, cross-owner and ambient denial. |
| AC-09 | `python -m pytest tests/acceptance/test_delivery_replay_restart.py --runxfail -q` | Crash after commit/before publish and before/after callback checkpoint; at-least-once redelivery; idempotency; durable offsets; poison isolation; reset/replay; ambiguous outbound reconciliation; no loss. |
| AC-10 | `python -m pytest tests/acceptance/test_adapter_categories.py --runxfail -q` | One credential-free example for Cat-A/B/C/D/E; multi-capability, verb allowlists, sensitivity floors, sharing models, central/bridge/dual identity, durable watermarks/circuits, install validation. |
| AC-11 | `python -m pytest tests/acceptance/test_guarded_write_observation.py --runxfail -q` | Table-driven first-failure proof for allowlist -> governance -> rate limit; independent OpenClaw/domain approvals; send modes; content/persona/privacy guard; every terminal audit; 5–7-day observation; full suppression payload; Cat-B rule. |
| AC-12 | `./scripts/acceptance/openclaw-contract.sh` | Host reports `v2026.7.1`; release commit is `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4`; npm integrity is the ADR-0001 value; plugin API and peer pins equal `2026.7.1`; native manifest/build/install/enable/restart/Doctor; typed skill, slash command, standing order, scoped conversation bridge; unavailable/incompatible fails closed; no provider SDK/direct call/local router. |
| AC-13 | `python -m pytest tests/acceptance/test_workbook_roundtrip.py --runxfail -q` | Both named workbooks; forward build, protected/derived rejection, editable reverse events, sidecars/locks, conflict visibility, Plaid columns immutable, concurrent retry, rebuild/replay equivalence, LibreOffice formula check. |
| AC-14 | `python -m pytest tests/acceptance/test_degraded_operation_and_doctor.py --runxfail -q` | Empty/disabled/stale/degraded distinct; adapter watermarks/circuits; poison consumer; OpenClaw/source/workbook outage; safe reads continue; writes fail closed; Doctor actionable and secret-free. |
| AC-15 | `./scripts/acceptance/backup-restore.sh` | Online-safe encrypted backup plus manifests; destroy disposable instance; restore on fresh path; validate log/projections/workbooks/offsets/config/plugin reconciliation; replay; wrong key/tamper fails. |
| AC-16 | `./scripts/acceptance/tenant-isolation.sh` | Static email/phone/address/name and hardcoded-instance-path scan; start two instances with distinct keys/tenants/ports; concurrent identical ids cannot cross log, offsets, projections, cache, files, HTTP, OpenClaw session, or backup. |
| AC-17 | `python -m pytest tests/acceptance/test_pack_lifecycle_profiles_personas.py --runxfail -q` | All six pack kinds validate dependencies, stage/test/build, atomic install, event, rollback; five profiles and four personas compile/behave; tenant-neutral scan; profile/persona cannot change authority/model routing. |
| AC-18 | `cd console && npm test -- --runInBand` plus `./scripts/acceptance/browser-e2e.sh` | Eight surfaces, three modes, child denial, calendar redaction, one reward toast under dual path, banners, evidence, states, responsive breakpoints, WCAG 2.2 AA; superseded capture flows absent; sanitized rendering. |
| AC-19 | `python -m pytest tests/acceptance/test_network_boundary.py --runxfail -q` | Only expected loopback listeners; Tailscale Serve is sole edge; spoofed identity headers rejected; legacy ports absent or tested aliases; routes preserve product ownership; member bridge endpoint least-privilege. |
| AC-20 | `python -m pytest tests/acceptance/test_migrations_shutdown.py --runxfail -q` | Every supported old schema fixture migrates/upcasts or fails before mutation; backup made; SIGTERM under ingest/pipeline/xlsx work drains/checkpoints/closes within bound; restart is consistent; forced-kill recovery proven. |
| AC-21 | `./scripts/acceptance/macos-bootstrap-rehearsal.sh` | Disposable clean macOS Phase-A host; interruption/resume and rollback per bootstrap section; per-member bridge + kid restrictions; observation briefing; signed report; target family Mac untouched. |
| AC-22 | `./scripts/acceptance/architecture-negative-checks.sh` | No direct provider SDK/call, duplicate chat/memory/scheduler/router/gateway/app builder, Vellum runtime, plaintext secret, projection write from adapter/pipeline, or required capability marked deferred. |

## Baseline and development commands

These describe the existing repository, not a release pass:

```bash
PYTHONPATH=bridge python -m pytest -q
python -m pytest bridge/tests -q
ruff check .
ruff check adminme bridge/adminme_bridge
mypy adminme bridge/adminme_bridge
bash scripts/verify_invariants.sh
cd console && npm ci && npm test
```

Gate 2 replaces the runtime spine and adds its own process-boundary acceptance test. The two end-to-end product slices remain strict Phase 3 targets:

```bash
PYTHONPATH=bridge python -m pytest tests/acceptance/test_communication_commitment_vertical_slice.py --runxfail -q
PYTHONPATH=bridge python -m pytest tests/acceptance/test_member_bridge_knowledge_vertical_slice.py --runxfail -q
```

## Gate 2 status

AC-02 is implemented by `adminme.runtime.RuntimeApplication` and `tests/acceptance/test_runtime_spine.py`. The test boots a real runtime process, verifies readiness and the exact 13-projection registry, ingests through `/api/bridge`, performs bounded shutdown, and reopens the encrypted log. AC-03 and AC-04 intentionally remain strict xfail skeletons; ordinary pytest may report them as expected xfails, while `--runxfail` must continue to fail until Phase 3 closes the product loops.

## Evidence schema

Each gate writes JSON containing `gate_id`, git SHA, dirty flag, UTC time, OS/arch, runtime/dependency/OpenClaw versions and integrity, command, exit code, duration, counts, unexpected skips/xfails, fixture ids, artifact hashes, and redacted failure summary. A signed aggregate references logs/screenshots/backups without embedding credentials or private content. Human visual/accessibility review supplements but never replaces automated authorization/behavior tests.
