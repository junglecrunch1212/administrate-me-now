# Release acceptance contract

Gate 7 status (2026-08-04): **household experience PASS at the
credential-free process and rendered-browser boundaries.** AC-18 now targets
the Python-served member portal rather than the deleted Node scaffold. The
Gate 7 acceptance test proves the non-admin adult OpenClaw chat seam, signed
per-member isolation, no-event ordinary conversation, exactly-once explicit
domain proposals, role/direct-route denial, all eight surfaces and three
modes, immediate daily-loop recalculation, and data-only temporary-surface
isolation/lifecycle while retaining exactly thirteen projections. The pinned
Edge harness proves responsive, reduced-motion, chat/degraded, and
private/promoted/archived quest states. Live provider and supported-Mac claims
remain AC-12/21 work.

Gate 6 status (2026-08-03): **domain convergence PASS at the credential-free
production-composition boundary.** AC-04's bridge/private-knowledge slice now
passes, and the full suite supplies executable Gate 6 coverage for all 13
projections, Cat-A–E, both workbooks, contextual ranking, pack/profile/persona
lifecycle, cursor/circuit recovery, notification policy/result ingestion,
capability truth, activity evidence, schema history, lint/types/builds, and
negative architecture checks. Live provider grants and the full browser UX
remain separate release evidence, not fixture claims.

Gate 5 status (2026-08-03): **AC-01 and AC-15 credential-free process portions PASS**. The ten-section installer, fixed operations surface, synthetic Doctor round trip, encrypted backup, validated restore, launchd contracts, secret redaction, bridge restrictions, and upgrade compatibility refusal pass locally. `macos-bootstrap.yml` and the live-Mac checklist cover what the Windows host cannot prove. Live OpenClaw, macOS grants, OAuth, and family activation remain unclaimed. AC-04 is now the Phase 6 product slice.

## Rules

These are release gates, not aspirational checks. A gate passes only from a clean checkout on the stated supported platform with production composition, credential-free fixtures/fake services, retained machine-readable evidence, and no unexpected skip/xfail. Unit mocks cannot substitute for a process boundary named in a gate. Golden skeletons are marked strict xfail during refactor; the release command uses `--runxfail`, so missing capability fails loudly.

Standard environment: supported macOS lab, Python 3.11, a Node version satisfying the exact OpenClaw source engine (currently `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`), exact lockfiles, exact pinned OpenClaw commit, loopback-only runtime, temporary encrypted instance directories, no real household account. Every command below must be wrapped by a versioned script/Make target; the current command is the intended interface.

## Gates

| ID | Executable release requirement | Required evidence / pass condition |
|---|---|---|
| AC-00 | `git diff --check && git status --porcelain` after build/test cleanup | Clean tracked tree; generated evidence stored only in declared artifact directory; no secret/unrelated file. |
| AC-01 | `./scripts/acceptance/clean-install.sh` | **Gate 5 process PASS:** encrypted ten-section fixture install, observation on, secret-reference scan clean, repeat run no-op, Doctor healthy. Disposable-live-mac grants remain AC-21 evidence. |
| AC-02 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_runtime_spine.py -q` | One Python process composes the encrypted log, durable bus, exact 13 projections, pipelines, adapters/xlsx, product namespaces, and bridge ingest; append commits before waking consumers; readiness, shutdown, and encrypted reopen are truthful. |
| AC-03 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_communication_commitment_vertical_slice.py -q` | **Gate 3 PASS:** credential-free inbound communication -> encrypted durable event -> bus -> Parties/Interactions -> schema-bound OpenClaw fixture provenance -> proposal-only review -> independent principal approval and observation-gated delivery intent -> API/xlsx -> complete/defer/snooze/block/unblock outcomes -> changed deterministic what-now. Duplicate requests, stale writes, wrong owners/roles, bad remote responses, replay, projection rebuild, crash-after-append, and restart preserve semantics. |
| AC-04 | `python -m pytest tests/acceptance/test_member_bridge_knowledge_vertical_slice.py -q` | **Gate 6 PASS:** enrolled member bridge -> owner-stamped knowledge event -> `member_knowledge` and allowed `vector_search`; another member/child/ambient/OpenClaw generic memory cannot read it; correction/replay proven. |
| AC-05 | `python -m pytest tests/acceptance/test_semantic_separation_and_feedback.py --runxfail -q` | Calendar/task/commitment/list/recurrence/scheduler schemas and stores remain distinct; Cat-B external edit round-trips; completion/defer/snooze/block/correction updates linked projections without type collapse. |
| AC-06 | `python -m pytest tests/acceptance/test_contextual_whatnow.py --runxfail -q` | Controlled clock/context fixtures prove different explainable results for now/hour/daypart/day/week/longer, energy/effort, availability, blockers, profile, scope, freshness, and lifecycle feedback. |
| AC-07 | `python -m pytest tests/acceptance/test_encryption_and_all_projections.py --runxfail -q` | Raw DB lacks plaintext sentinel and rejects wrong key; exact 13-name registry; each projection consumes a canonical event; full replay is semantically/byte equivalent as defined; no privileged vector row. |
| AC-08 | `python -m pytest tests/acceptance/test_member_child_authorization.py --runxfail -q` | `authMember`/`viewMember`, owner/visibility/sensitivity, trusted-proxy identity, privileged audit, calendar busy redaction, child direct-route denial, narrow portal, cross-owner and ambient denial. |
| AC-09 | `python -m pytest tests/acceptance/test_delivery_replay_restart.py --runxfail -q` | Crash after commit/before publish and before/after callback checkpoint; at-least-once redelivery; idempotency; durable offsets; poison isolation; reset/replay; ambiguous outbound reconciliation; no loss. |
| AC-10 | `python -m pytest tests/acceptance/test_adapter_categories.py --runxfail -q` | One credential-free example for Cat-A/B/C/D/E; multi-capability, verb allowlists, sensitivity floors, sharing models, central/bridge/dual identity, durable watermarks/circuits, install validation. |
| AC-11 | `python -m pytest tests/acceptance/test_guarded_write_observation.py --runxfail -q` | Table-driven first-failure proof for allowlist -> governance -> rate limit; independent OpenClaw/domain approvals; send modes; content/persona/privacy guard; every terminal audit; 5–7-day observation; full suppression payload; Cat-B rule. |
| AC-12 | `npm --prefix openclaw/administrateme run check && npm --prefix openclaw/administrateme test && python -m pytest tests/acceptance/test_openclaw_native_seam.py -q`; then in disposable macOS lab `npm --prefix openclaw/administrateme run test:live:mac` | Package/API/gateway/build pins equal `2026.7.2` and source commit `944cf08f94c334cff2b0fdf7e01187a9efa42715`; native manifest/build/pack; typed tools and dashboard; skill, slash, standing-order, scoped bridge; metadata-only model provenance; actor/member denial; observation; exact-once outcomes; durable unavailable/restart; incompatible fixture rejection. Fixture/process PASS is distinct from the required live link/install/inspect/restart/deep-RPC macOS sub-gate. |
| AC-13 | `python -m pytest tests/acceptance/test_workbook_roundtrip.py --runxfail -q` | Both named workbooks; forward build, protected/derived rejection, editable reverse events, sidecars/locks, conflict visibility, Plaid columns immutable, concurrent retry, rebuild/replay equivalence, LibreOffice formula check. |
| AC-14 | `python -m pytest tests/acceptance/test_degraded_operation_and_doctor.py --runxfail -q` | Empty/disabled/stale/degraded distinct; adapter watermarks/circuits; poison consumer; OpenClaw/source/workbook outage; safe reads continue; writes fail closed; Doctor actionable and secret-free. |
| AC-15 | `./scripts/acceptance/backup-restore.sh` | **Gate 5 process PASS:** quiesced encrypted backup plus manifest; authenticated staging restore; active state untouched until validation; rollback copy; all 13 projections/workbooks rebuilt from the event log. Wrong-key/tamper paths fail closed. |
| AC-16 | `./scripts/acceptance/tenant-isolation.sh` | Static email/phone/address/name and hardcoded-instance-path scan; start two instances with distinct keys/tenants/ports; concurrent identical ids cannot cross log, offsets, projections, cache, files, HTTP, OpenClaw session, or backup. |
| AC-17 | `python -m pytest tests/acceptance/test_pack_lifecycle_profiles_personas.py --runxfail -q` | All six pack kinds validate dependencies, stage/test/build, atomic install, event, rollback; five profiles and four personas compile/behave; tenant-neutral scan; profile/persona cannot change authority/model routing. |
| AC-18 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_gate7_household_experience.py -q && npm --prefix tools/console-reference-harness run test:gate7` | **Gate 7 PASS:** eight Python-served surfaces; carousel/compressed/child modes; independent child/device/direct-route denial; observation/degraded/chat/quest states; deterministic desktop/tablet/narrow captures; named controls, heading/reflow/reduced-motion checks; sanitized DOM rendering; no unexpected browser error/request failure. Calendar privacy, reward dedupe, guarded-write order, and final-outbound behavior retain their lower-layer executable suites. |
| AC-19 | `python -m pytest tests/acceptance/test_network_boundary.py --runxfail -q` | Only expected loopback listeners; Tailscale Serve is sole edge; spoofed identity headers rejected; legacy ports absent or tested aliases; routes preserve product ownership; member bridge endpoint least-privilege. |
| AC-20 | `python -m pytest tests/acceptance/test_migrations_shutdown.py --runxfail -q` | Every supported old schema fixture migrates/upcasts or fails before mutation; backup made; SIGTERM under ingest/pipeline/xlsx work drains/checkpoints/closes within bound; restart is consistent; forced-kill recovery proven. |
| AC-21 | `./scripts/acceptance/macos-bootstrap-rehearsal.sh` | Gate 5 supplies the reproducible credential-free macOS job and live checklist. A disposable Apple-silicon live rehearsal must still record grants, crash/login recovery, real pinned Gateway health, and a signed report; target family Mac remains untouched. |
| AC-22 | `./scripts/acceptance/architecture-negative-checks.sh` | No direct provider SDK/call, duplicate chat/memory/scheduler/router/gateway/app builder, Vellum runtime, plaintext secret, projection write from adapter/pipeline, or required capability marked deferred. |
| AC-23 | `PYTHONPATH=bridge python -m pytest tests/acceptance/test_gate7_household_experience.py tests/unit/test_member_chat_gate7.py -q && npm --prefix openclaw/administrateme run check && npm --prefix openclaw/administrateme test` | Portal chat is a cancellable/backpressure-bounded exact-protocol OpenClaw relay; two members cannot cross-read transcript/session/tool output; no credential/raw envelope leaves the boundary; child tools are independently allowlisted. Temporary views are atomic presentation config over allowlisted scope-filtered reads, default private, reviewed for promotion/child exposure, and disappear without changing domain facts, history, or the 13-projection registry. |

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

Gate 5 adds the bootstrap/operations process gate. The next end-to-end product slice remains strict Phase 6 work:

```bash
PYTHONPATH=bridge python -m pytest tests/acceptance/test_communication_commitment_vertical_slice.py --runxfail -q
PYTHONPATH=bridge python -m pytest tests/acceptance/test_member_bridge_knowledge_vertical_slice.py --runxfail -q
```

## Gate 5 status

AC-02, AC-03, and AC-12's non-live portion remain implemented. Gate 5 implements AC-01 and AC-15 at the credential-free production-composition boundary through `tests/acceptance/test_macos_bootstrap_operations.py`. The same package provides `scripts/acceptance/macos-bootstrap-rehearsal.sh`, `.github/workflows/macos-bootstrap.yml`, and the human checklist in `MAC-MINI-OPERATIONS.md`. This is installer readiness, not evidence that a live provider or the future family Mac works. AC-04 remains the strict Phase 6 xfail.

## Evidence schema

Each gate writes JSON containing `gate_id`, git SHA, dirty flag, UTC time, OS/arch, runtime/dependency/OpenClaw versions and integrity, command, exit code, duration, counts, unexpected skips/xfails, fixture ids, artifact hashes, and redacted failure summary. A signed aggregate references logs/screenshots/backups without embedding credentials or private content. Human visual/accessibility review supplements but never replaces automated authorization/behavior tests.
